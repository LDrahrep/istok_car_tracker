from __future__ import annotations

import time
import logging
from typing import Optional

import gspread
import difflib
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

from models import (
    Driver,
    DriverPassengers,
    Employee,
    ShiftType,
    SheetError,
    normalize_text,
)

logger = logging.getLogger(__name__)

# Кеш чтений: 60 секунд достаточно чтобы еженедельная рассылка
# прочитала employees и drivers_passengers один раз вместо ~270.
_CACHE_TTL = 60  # seconds

# Retry при 429 (quota exceeded)
_RETRY_MAX = 3
_RETRY_BASE_WAIT = 10  # seconds


class SheetManager:
    def __init__(self, config):
        self.config = config
        self.client = self._build_client()
        self._spreadsheet = None
        self._cache: dict[str, tuple[float, list]] = {}
        self._ws_cache: dict[str, object] = {}

    # -------------------------
    # Google client
    # -------------------------

    def _build_client(self):
        import json

        info = json.loads(self.config.GOOGLE_CREDENTIALS)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        return gspread.authorize(creds)

    def _retry(self, fn):
        """Execute fn with retry on 429 quota errors."""
        for attempt in range(_RETRY_MAX + 1):
            try:
                return fn()
            except APIError as e:
                if e.response.status_code == 429 and attempt < _RETRY_MAX:
                    wait = (attempt + 1) * _RETRY_BASE_WAIT
                    logger.warning(
                        "Sheets API quota exceeded, retry %d/%d in %ds",
                        attempt + 1, _RETRY_MAX, wait,
                    )
                    time.sleep(wait)
                    self._spreadsheet = None
                    continue
                raise

    def _open(self):
        """Return cached Spreadsheet object (one API call on first use)."""
        if self._spreadsheet is None:
            self._spreadsheet = self._retry(
                lambda: self.client.open_by_key(self.config.SPREADSHEET_ID)
            )
        return self._spreadsheet

    def _ws(self, name: str):
        if name not in self._ws_cache:
            self._ws_cache[name] = self._retry(
                lambda: self._open().worksheet(name)
            )
        return self._ws_cache[name]

    def _values(self, name: str):
        now = time.monotonic()
        cached = self._cache.get(name)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        values = self._retry(lambda: self._ws(name).get_all_values())
        self._cache[name] = (time.monotonic(), values)
        return values

    def _invalidate(self, name: str):
        self._cache.pop(name, None)
        self._ws_cache.pop(name, None)

    # -------------------------
    # helpers
    # -------------------------

    @staticmethod
    def _cell_eq(cell: str, tg_id: int) -> bool:
        """Строгое сравнение TGID по требованиям: str(cell).strip() == str(tg_id)."""
        return str(cell).strip() == str(tg_id)

    @staticmethod
    def _col_map(headers):
        return {h.strip(): i for i, h in enumerate(headers)}

    @staticmethod
    def _col_letter(idx: int) -> str:
        """0-based index → A1 notation (A, B, ..., Z, AA, AB, ...)."""
        result = ""
        while True:
            result = chr(ord('A') + idx % 26) + result
            idx = idx // 26 - 1
            if idx < 0:
                break
        return result

    @staticmethod
    def _col_get(col: dict, *keys):
        """Get column index, trying keys in order. Safe for index 0."""
        for k in keys:
            v = col.get(k)
            if v is not None:
                return v
        return None

    @staticmethod
    def _row_dict(headers, row):
        return {
            headers[i]: row[i] if i < len(row) else ""
            for i in range(len(headers))
        }

    # =========================
    # Employees
    # =========================

    def get_all_employees(self) -> list[Employee]:
        values = self._values(self.config.EMPLOYEES_SHEET)
        if not values or len(values) < 2:
            return []

        headers = values[0]
        return [
            Employee.from_row(self._row_dict(headers, r))
            for r in values[1:]
        ]

    def get_employee_by_name(self, name: str) -> Optional[Employee]:
        n = normalize_text(name)
        for e in self.get_all_employees():
            if normalize_text(e.name) == n:
                return e
        return None

    def get_employee_by_tgid(self, tg_id: int) -> Optional[Employee]:
        for e in self.get_all_employees():
            if e.tg_id and int(e.tg_id) == int(tg_id):
                return e
        return None

    def get_shift_for_tgid(self, tg_id: int) -> ShiftType:
        emp = self.get_employee_by_tgid(tg_id)
        return ShiftType.from_string(emp.shift if emp else "")

    # =========================
    # Drivers
    # =========================

    def get_all_driver_tgids(self) -> list[int]:
        """Получить все уникальные telegramID из таблицы drivers."""
        values = self._values(self.config.DRIVERS_SHEET)
        if not values or len(values) < 2:
            return []

        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        if tg_col is None:
            return []

        seen = set()
        result = []
        for row in values[1:]:
            if tg_col < len(row):
                raw = str(row[tg_col]).strip()
                if raw.isdigit() and raw not in seen:
                    seen.add(raw)
                    result.append(int(raw))
        return result

    def get_driver(self, tg_id: int) -> Optional[Driver]:
        values = self._values(self.config.DRIVERS_SHEET)
        if not values or len(values) < 2:
            return None

        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")

        if tg_col is None:
            return None

        for row in values[1:]:
            if tg_col < len(row) and self._cell_eq(row[tg_col], tg_id):
                return Driver.from_row(self._row_dict(headers, row))

        return None

    def is_name_taken_by_other_driver(self, name: str, tg_id: int) -> bool:
        """Проверить, зарегистрировал ли другой водитель (другой tg_id) это имя."""
        values = self._values(self.config.DRIVERS_SHEET)
        if not values or len(values) < 2:
            return False

        headers = values[0]
        col = self._col_map(headers)
        name_col = col.get("Name")
        tg_col = col.get("telegramID")
        if name_col is None or tg_col is None:
            return False

        n = normalize_text(name)
        for row in values[1:]:
            if name_col < len(row) and normalize_text(row[name_col]) == n:
                if tg_col < len(row):
                    raw = str(row[tg_col]).strip()
                    if raw.isdigit() and int(raw) != tg_id:
                        return True
        return False

    def upsert_driver(self, driver: Driver):
        values = self._values(self.config.DRIVERS_SHEET)
        if not values:
            raise SheetError("drivers sheet empty")

        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        if tg_col is None:
            raise SheetError("telegramID column not found in drivers")
        ws = self._ws(self.config.DRIVERS_SHEET)

        existing = None
        for i, row in enumerate(values[1:], start=2):
            if tg_col < len(row) and self._cell_eq(row[tg_col], driver.tg_id):
                existing = i
                break

        # ВАЖНО: обновляем ТОЛЬКО те колонки, которые управляются ботом.
        # Это позволяет не затирать вручную заполненные поля (например Shift).
        if existing:
            updates = []

            def put(key: str, value: str):
                idx = col.get(key)
                if idx is None:
                    return
                col_letter = SheetManager._col_letter(idx)
                updates.append({"range": f"{col_letter}{existing}", "values": [[value]]})

            put("Name", driver.name)
            # telegramID обязателен
            col_letter = SheetManager._col_letter(tg_col)
            updates.append({"range": f"{col_letter}{existing}", "values": [[str(driver.tg_id)]]})
            put("Car", driver.car)
            put("Plates", driver.plates)
            put("isActive", "TRUE" if driver.is_active else "FALSE")

            ws.batch_update(updates)
        else:
            # Для новой строки заполняем известные поля, остальные оставляем пустыми.
            row_out = [""] * len(headers)

            if "Name" in col:
                row_out[col["Name"]] = driver.name
            row_out[tg_col] = str(driver.tg_id)

            if "Car" in col:
                row_out[col["Car"]] = driver.car
            if "Plates" in col:
                row_out[col["Plates"]] = driver.plates
            if "isActive" in col:
                row_out[col["isActive"]] = "TRUE" if driver.is_active else "FALSE"

            ws.append_row(row_out, value_input_option="USER_ENTERED")

        self._invalidate(self.config.DRIVERS_SHEET)

    def delete_driver(self, tg_id: int):
        values = self._values(self.config.DRIVERS_SHEET)
        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        if tg_col is None:
            return
        ws = self._ws(self.config.DRIVERS_SHEET)

        for i, row in enumerate(values[1:], start=2):
            if tg_col < len(row) and self._cell_eq(row[tg_col], tg_id):
                ws.delete_rows(i)
                break

        self._invalidate(self.config.DRIVERS_SHEET)

    # =========================
    # DriverPassengers
    # =========================

    def get_driver_passengers(self, tg_id: int) -> Optional[DriverPassengers]:
        values = self._values(self.config.DRIVERS_PASSENGERS_SHEET)
        if not values or len(values) < 2:
            return None

        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")

        for row in values[1:]:
            if tg_col < len(row) and self._cell_eq(row[tg_col], tg_id):
                return DriverPassengers.from_row(
                    self._row_dict(headers, row)
                )

        return None

    def upsert_driver_passengers(self, dp: DriverPassengers):
        values = self._values(self.config.DRIVERS_PASSENGERS_SHEET)
        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        if tg_col is None:
            raise SheetError("telegramID column not found in drivers_passengers")
        ws = self._ws(self.config.DRIVERS_PASSENGERS_SHEET)

        existing = None
        for i, row in enumerate(values[1:], start=2):
            if tg_col < len(row) and self._cell_eq(row[tg_col], dp.driver_tgid):
                existing = i
                break

        # ВАЖНО: при обновлении не затираем неуправляемые колонки (например Shift).
        if existing:
            updates = []

            def put(key: str, value: str):
                idx = col.get(key)
                if idx is None:
                    return
                col_letter = SheetManager._col_letter(idx)
                updates.append({"range": f"{col_letter}{existing}", "values": [[value]]})

            put("Name", dp.driver_name)
            col_letter = SheetManager._col_letter(tg_col)
            updates.append({"range": f"{col_letter}{existing}", "values": [[str(dp.driver_tgid)]]})

            for i, key in enumerate(("Passenger1", "Passenger2", "Passenger3", "Passenger4")):
                put(key, dp.passengers[i] if i < len(dp.passengers) else "")

            ws.batch_update(updates)
        else:
            row_out = [""] * len(headers)

            if "Name" in col:
                row_out[col["Name"]] = dp.driver_name
            row_out[tg_col] = str(dp.driver_tgid)

            for idx, key in enumerate(("Passenger1", "Passenger2", "Passenger3", "Passenger4")):
                if key in col:
                    row_out[col[key]] = dp.passengers[idx] if idx < len(dp.passengers) else ""

            ws.append_row(row_out, value_input_option="USER_ENTERED")

        self._invalidate(self.config.DRIVERS_PASSENGERS_SHEET)

    def delete_driver_passengers(self, tg_id: int) -> bool:
        """Удалить строку водителя из drivers_passengers по TGID."""
        values = self._values(self.config.DRIVERS_PASSENGERS_SHEET)
        if not values or len(values) < 2:
            return False

        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        if tg_col is None:
            return False

        ws = self._ws(self.config.DRIVERS_PASSENGERS_SHEET)
        for i, row in enumerate(values[1:], start=2):
            if tg_col < len(row) and self._cell_eq(row[tg_col], tg_id):
                ws.delete_rows(i)
                self._invalidate(self.config.DRIVERS_PASSENGERS_SHEET)
                return True

        return False

    def clear_rides_with(
        self,
        *,
        tg_ids: set[int] | None = None,
        names: set[str] | None = None,
    ) -> int:
        """Очистить employees.Rides with И employees.telegramID для сотрудников.

        Поиск по tg_ids ищет по колонке telegramID (= ID водителя),
        поиск по names ищет по колонке Employee/Name.

        Возвращает число обновлённых строк.
        """
        tg_ids = tg_ids or set()
        names_norm = {normalize_text(n) for n in (names or set()) if n}

        values = self._values(self.config.EMPLOYEES_SHEET)
        if not values or len(values) < 2:
            return 0

        headers = values[0]
        col = self._col_map(headers)

        tg_col = self._col_get(col, "telegramID", "telegramid")
        name_col = self._col_get(col, "Employee", "Name")
        rides_col = col.get("Rides with")

        if rides_col is None:
            return 0

        ws = self._ws(self.config.EMPLOYEES_SHEET)
        updates = []
        matched = 0

        for idx, row in enumerate(values[1:], start=2):
            row_tg = None
            if tg_col is not None and tg_col < len(row):
                raw = str(row[tg_col]).strip()
                if raw.isdigit():
                    row_tg = int(raw)

            row_name = ""
            if name_col is not None and name_col < len(row):
                row_name = str(row[name_col] or "")

            match = False
            if row_tg is not None and row_tg in tg_ids:
                match = True
            if not match and row_name and normalize_text(row_name) in names_norm:
                match = True

            if not match:
                continue

            matched += 1

            # Очищаем Rides with
            rides_letter = SheetManager._col_letter(rides_col)
            updates.append({"range": f"{rides_letter}{idx}", "values": [[""]]})

            # Очищаем telegramID
            if tg_col is not None:
                tg_letter = SheetManager._col_letter(tg_col)
                updates.append({"range": f"{tg_letter}{idx}", "values": [[""]]})

        if not updates:
            return 0

        ws.batch_update(updates)
        self._invalidate(self.config.EMPLOYEES_SHEET)
        return matched

    def assign_passengers_to_driver(
        self,
        driver_tgid: int,
        driver_name: str,
        passenger_names: list[str],
    ) -> int:
        """Записать в employees.Rides_with и employees.telegramID данные водителя
        для каждого пассажира (по имени). Также записывает водителя самого к себе.

        Возвращает число обновлённых строк.
        """
        names_to_assign = {normalize_text(n) for n in passenger_names if n}
        # Водитель тоже приписывается к себе
        names_to_assign.add(normalize_text(driver_name))

        values = self._values(self.config.EMPLOYEES_SHEET)
        if not values or len(values) < 2:
            return 0

        headers = values[0]
        col = self._col_map(headers)

        name_col = self._col_get(col, "Employee", "Name")
        rides_col = col.get("Rides with")
        tg_col = self._col_get(col, "telegramID", "telegramid")

        if name_col is None or rides_col is None or tg_col is None:
            return 0

        ws = self._ws(self.config.EMPLOYEES_SHEET)
        updates = []

        for idx, row in enumerate(values[1:], start=2):
            row_name = ""
            if name_col < len(row):
                row_name = str(row[name_col] or "")

            if not row_name or normalize_text(row_name) not in names_to_assign:
                continue

            # Записываем Rides with = имя водителя
            rides_letter = SheetManager._col_letter(rides_col)
            updates.append({"range": f"{rides_letter}{idx}", "values": [[driver_name]]})

            # Записываем telegramID = ID водителя
            tg_letter = SheetManager._col_letter(tg_col)
            updates.append({"range": f"{tg_letter}{idx}", "values": [[str(driver_tgid)]]})

        if not updates:
            return 0

        ws.batch_update(updates)
        self._invalidate(self.config.EMPLOYEES_SHEET)
        return len(updates) // 2  # каждый сотрудник = 2 обновления

    # =========================
    # Passenger lookup
    # =========================

    def find_driver_for_passenger(self, passenger_name: str) -> Optional[tuple[int, str]]:
        """Найти водителя, у которого указанный сотрудник записан пассажиром.

        В таблице drivers_passengers пассажиры хранятся по имени (Passenger1..Passenger4),
        поэтому ищем по нормализованному имени.

        Возвращает (driver_tgid, driver_name) или None.
        """
        n = normalize_text(passenger_name)
        if not n:
            return None

        values = self._values(self.config.DRIVERS_PASSENGERS_SHEET)
        if not values or len(values) < 2:
            return None

        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        name_col = col.get("Name")

        passenger_cols = [
            col.get("Passenger1"),
            col.get("Passenger2"),
            col.get("Passenger3"),
            col.get("Passenger4"),
        ]

        for row in values[1:]:
            hit = False
            for pc in passenger_cols:
                if pc is None or pc >= len(row):
                    continue
                if normalize_text(row[pc]) == n:
                    hit = True
                    break
            if not hit:
                continue

            driver_tg = None
            if tg_col is not None and tg_col < len(row):
                raw = str(row[tg_col]).strip()
                if raw.isdigit():
                    driver_tg = int(raw)

            driver_name = ""
            if name_col is not None and name_col < len(row):
                driver_name = str(row[name_col] or "").strip()

            if driver_tg is None:
                continue

            return driver_tg, driver_name

        return None

    # =========================
    # Validation
    # =========================

    def validate_passengers(
        self, driver_tgid: int, names: list[str]
    ) -> tuple[list[Employee], list[str], list[str]]:
        """Валидация пассажиров.

        ВАЖНО: в employees.telegramID хранится ID ВОДИТЕЛЯ (не сотрудника).
        Поэтому смену водителя берём из таблицы drivers, а проверку
        «водитель = пассажир» делаем ТОЛЬКО по имени.

        Критерии добавления пассажира:
        1. Сотрудник существует в employees
        2. Одна смена с водителем (Day/Meltech = одно)
        3. employees.telegramID и Rides with пустые (свободен)
        4. Если telegramID = ID этого водителя → уже приписан к тебе
        5. Если занят другим водителем → ошибка
        """
        errors: list[str] = []
        warnings: list[str] = []
        valid: list[Employee] = []
        already_assigned: list[str] = []

        # Смену водителя берём из таблицы drivers (там Shift точно его)
        driver_record = self.get_driver(driver_tgid)
        if not driver_record:
            errors.append("⛔ Ты не зарегистрирован как водитель.")
            return [], errors, warnings

        driver_shift = ShiftType.from_string(driver_record.shift)
        driver_name_norm = normalize_text(driver_record.name)

        if driver_shift == ShiftType.UNKNOWN:
            errors.append(
                "⛔ У тебя не указана смена.\n"
                "Обратись к администратору."
            )
            return [], errors, warnings

        all_employees = [e for e in self.get_all_employees() if e.name]

        employees = {
            normalize_text(e.name): e
            for e in all_employees
        }

        # кандидаты для подсказок: только сотрудники той же смены
        same_shift_names = [
            e.name
            for e in all_employees
            if ShiftType.from_string(e.shift) == driver_shift
        ]

        seen = set()

        for raw in names:
            n = normalize_text(raw)
            if not n or n in seen:
                continue

            seen.add(n)
            emp = employees.get(n)

            if not emp:
                suggestions = difflib.get_close_matches(
                    raw.strip(),
                    same_shift_names,
                    n=3,
                    cutoff=0.68,
                )
                if suggestions:
                    warnings.append(
                        f"• {raw}: сотрудника еще не добавили. Возможно, ты имел в виду: "
                        + ", ".join(suggestions)
                    )
                else:
                    warnings.append(f"• {raw}: сотрудника еще не добавили.")
                continue

            # Водитель не может быть пассажиром (сравниваем по имени)
            if n == driver_name_norm:
                warnings.append(
                    "🙃 Водитель не может быть пассажиром — этот пункт пропущен.\n"
                    "Если ты больше не водитель, нажми «🛑 Перестать быть водителем», "
                    "и тогда тебя смогут добавить пассажиром."
                )
                continue

            # Проверка смены
            p_shift = ShiftType.from_string(emp.shift)
            if p_shift != driver_shift:
                warnings.append(f"• {emp.name}: сотрудник в другой смене.")
                continue

            # Проверка занятости: employees.telegramID и Rides with
            rides_with = (emp.rides_with or "").strip()
            has_driver_id = emp.tg_id is not None

            if rides_with or has_driver_id:
                # Уже приписан к кому-то
                is_mine = (
                    (emp.tg_id is not None and int(emp.tg_id) == int(driver_tgid))
                    or normalize_text(rides_with) == driver_name_norm
                )
                if is_mine:
                    already_assigned.append(emp.name)
                else:
                    warnings.append(
                        f"• {emp.name}: уже записан к другому водителю ({rides_with})."
                    )
                continue

            valid.append(emp)

        if len(valid) > 4:
            warnings.append("• Максимум 4 пассажира — лишние будут проигнорированы.")
            valid = valid[:4]

        # Все пассажиры уже приписаны к этому водителю
        if not valid and already_assigned and not warnings:
            errors.append(
                "ℹ️ Все указанные пассажиры уже приписаны к тебе:\n"
                + "\n".join(f"• {name}" for name in already_assigned)
            )
            return [], errors, []

        # Часть приписана, часть новая
        if already_assigned:
            for name in already_assigned:
                warnings.append(f"• {name}: уже приписан к тебе.")

        if not valid and not already_assigned:
            msg = (
                "❌ Никого не удалось добавить.\n\n"
                "Возможные причины:\n"
                "• сотрудника еще не добавили\n"
                "• сотрудник в другой смене\n"
                "• сотрудник уже записан к другому водителю\n\n"
            )
            if warnings:
                msg += "Что именно не подошло:\n" + "\n".join(warnings)
            errors.append(msg)


        return valid, errors, warnings