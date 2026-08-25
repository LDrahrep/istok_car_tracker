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
    normalize_sorted,
)

logger = logging.getLogger(__name__)

# Retry при 429 (quota exceeded)
_RETRY_MAX = 3
_RETRY_BASE_WAIT = 10  # seconds

# Микро-кеш: дедупликация чтений в рамках одной операции (< 1 сек).
# НЕ влияет на корректность: после каждой записи кеш инвалидируется,
# а TTL настолько мал, что между разными пользовательскими действиями
# всегда будут свежие данные.
_OP_CACHE_TTL = 3  # seconds


class SheetManager:
    def __init__(self, config):
        self.config = config
        self.client = self._build_client()
        self._spreadsheet = None
        self._ws_cache: dict[str, object] = {}
        self._op_cache: dict[str, tuple[float, list]] = {}

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
        """Execute fn with retry on transient Google API errors."""
        _RETRIABLE = {429, 500, 502, 503, 504}
        for attempt in range(_RETRY_MAX + 1):
            try:
                return fn()
            except APIError as e:
                if e.response.status_code in _RETRIABLE and attempt < _RETRY_MAX:
                    wait = (attempt + 1) * _RETRY_BASE_WAIT
                    logger.warning(
                        "Sheets API error %s, retry %d/%d in %ds",
                        e.response.status_code,
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
        now = time.time()
        cached = self._op_cache.get(name)
        if cached:
            ts, data = cached
            if now - ts < _OP_CACHE_TTL:
                return data
        data = self._retry(lambda: self._ws(name).get_all_values())
        self._op_cache[name] = (now, data)
        return data

    def _invalidate(self, name: str):
        """Сбросить микро-кеш для листа после записи."""
        self._op_cache.pop(name, None)

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
            logger.warning("get_all_employees: no data (rows=%d)", len(values) if values else 0)
            return []

        headers = values[0]
        logger.info(
            "get_all_employees: sheet=%s rows=%d headers=%r",
            self.config.EMPLOYEES_SHEET, len(values) - 1, headers,
        )

        employees = []
        for r in values[1:]:
            rd = self._row_dict(headers, r)
            emp = Employee.from_row(rd)
            employees.append(emp)

        with_name = [e for e in employees if e.name]
        logger.info(
            "get_all_employees: total=%d with_name=%d sample_names=%r",
            len(employees), len(with_name),
            [e.name for e in with_name[:5]],
        )
        return employees

    def get_employee_by_name(self, name: str) -> Optional[Employee]:
        n = normalize_text(name)
        n_sorted = normalize_sorted(name)
        all_emp = self.get_all_employees()
        logger.info(
            "get_employee_by_name: looking for %r (normalized=%r) in %d employees",
            name, n, len(all_emp),
        )

        reversed_match: Optional[Employee] = None
        for e in all_emp:
            if not e.name:
                continue
            en = normalize_text(e.name)
            if en == n:
                return e
            if reversed_match is None and normalize_sorted(e.name) == n_sorted:
                reversed_match = e

        if reversed_match:
            logger.info(
                "get_employee_by_name: matched %r via reversed-token lookup -> %r",
                name, reversed_match.name,
            )
            return reversed_match

        # Дополнительная диагностика: покажем первые 10 нормализованных имён
        sample = [normalize_text(e.name) for e in all_emp if e.name][:10]
        logger.warning(
            "get_employee_by_name: NOT FOUND %r among %d employees. Sample normalized: %r",
            n, len(all_emp), sample,
        )
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
            put("Username", driver.username)
            put("Car", driver.car)
            put("Plates", driver.plates)
            put("City", driver.city)
            put("State", driver.state)
            put("isActive", "TRUE" if driver.is_active else "FALSE")

            ws.batch_update(updates)
            self._invalidate(self.config.DRIVERS_SHEET)
        else:
            # Для новой строки заполняем известные поля, остальные оставляем пустыми.
            row_out = [""] * len(headers)

            if "Name" in col:
                row_out[col["Name"]] = driver.name
            row_out[tg_col] = str(driver.tg_id)

            for key, value in (
                ("Username", driver.username),
                ("Car", driver.car),
                ("Plates", driver.plates),
                ("City", driver.city),
                ("State", driver.state),
                ("isActive", "TRUE" if driver.is_active else "FALSE"),
            ):
                if key in col:
                    row_out[col[key]] = value

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
                self._invalidate(self.config.DRIVERS_SHEET)
                break


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
            self._invalidate(self.config.DRIVERS_PASSENGERS_SHEET)
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

    def clear_rides_with(self, *, names: set[str]) -> int:
        """Очистить employees.Rides with И employees.telegramID для сотрудников.

        Поиск ТОЛЬКО по имени (колонка Employee/Name).
        Возвращает число обновлённых строк.
        """
        names_norm = {normalize_text(n) for n in names if n}
        if not names_norm:
            return 0

        values = self._values(self.config.EMPLOYEES_SHEET)
        if not values or len(values) < 2:
            return 0

        headers = values[0]
        col = self._col_map(headers)

        tg_col = self._col_get(col, "DriverTGID", "telegramID", "telegramid")
        name_col = self._col_get(col, "Employee", "Name", "")
        rides_col = col.get("Rides with")

        if rides_col is None or name_col is None:
            return 0

        ws = self._ws(self.config.EMPLOYEES_SHEET)
        updates = []
        matched = 0

        for idx, row in enumerate(values[1:], start=2):
            row_name = ""
            if name_col < len(row):
                row_name = str(row[name_col] or "")

            if not row_name or normalize_text(row_name) not in names_norm:
                continue

            matched += 1

            rides_letter = SheetManager._col_letter(rides_col)
            updates.append({"range": f"{rides_letter}{idx}", "values": [[""]]})

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

        name_col = self._col_get(col, "Employee", "Name", "")
        rides_col = col.get("Rides with")
        tg_col = self._col_get(col, "DriverTGID", "telegramID", "telegramid")

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

        # Fallback: drivers.Shift заполняется ARRAYFORMULA, которая может не
        # успеть пересчитаться после регистрации. Берём смену напрямую
        # из employees по имени водителя.
        if driver_shift == ShiftType.UNKNOWN:
            emp_driver = self.get_employee_by_name(driver_record.name)
            if emp_driver:
                driver_shift = ShiftType.from_string(emp_driver.shift)

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
        # Индекс по отсортированным токенам для матчинга перевёрнутых имён
        # ("Ivanov Petr" -> "Petr Ivanov"). Если два сотрудника дают один
        # и тот же ключ, оставляем первого — это крайне маловероятный случай.
        employees_sorted = {}
        for e in all_employees:
            key = normalize_sorted(e.name)
            if key and key not in employees_sorted:
                employees_sorted[key] = e

        # кандидаты для подсказок: только сотрудники той же смены
        same_shift_names = [
            e.name
            for e in all_employees
            if ShiftType.from_string(e.shift) == driver_shift
        ]

        # Диагностика: логируем все смены
        shift_counts: dict[str, int] = {}
        for e in all_employees:
            st = ShiftType.from_string(e.shift).value
            shift_counts[st] = shift_counts.get(st, 0) + 1

        logger.info(
            "validate_passengers: driver_tgid=%d driver_shift=%s "
            "all_employees=%d same_shift=%d shift_distribution=%r",
            driver_tgid, driver_shift.value,
            len(all_employees), len(same_shift_names), shift_counts,
        )

        seen = set()

        for raw in names:
            n = normalize_text(raw)
            if not n or n in seen:
                continue

            seen.add(n)
            emp = employees.get(n)

            if not emp:
                emp = employees_sorted.get(normalize_sorted(raw))
                if emp:
                    logger.info(
                        "validate_passengers: matched %r via reversed-token lookup -> %r",
                        raw, emp.name,
                    )

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

            # --- Двойная роль ---
            # X может быть зарегистрированным водителем, но если у него НЕТ своих
            # пассажиров — его можно взять пассажиром (он сейчас сам никого не везёт).
            # Если у X есть свои пассажиры — нельзя (он активный водитель).
            x_driver = self.get_driver_by_name(emp.name)
            x_is_driver = x_driver is not None

            # Проверка занятости: employees.Rides with / DriverTGID.
            # Self-assignment (X приписан к самому себе как водитель) занятостью НЕ считаем.
            rides_with = (emp.rides_with or "").strip()
            has_driver_id = emp.tg_id is not None
            self_ref = x_is_driver and (
                (rides_with and normalize_text(rides_with) == normalize_text(emp.name))
                or (has_driver_id and int(emp.tg_id) == int(x_driver.tg_id))
            )

            if (rides_with or has_driver_id) and not self_ref:
                # Приписан к КОМУ-ТО другому (реальному водителю)
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

            # X свободен или сам себе водитель. Если это активный водитель со своими
            # пассажирами — брать пассажиром нельзя.
            if x_is_driver and self.driver_own_passenger_count(emp.name) >= 1:
                warnings.append(
                    f"• {emp.name}: сейчас сам возит пассажиров — его нельзя добавить."
                )
                continue

            # Вторая линия защиты: source of truth (drivers_passengers), кроме self.
            hit = self.find_driver_for_passenger(emp.name)
            if hit:
                other_tgid, other_name = hit
                if int(other_tgid) == int(driver_tgid):
                    already_assigned.append(emp.name)
                    continue
                if normalize_text(other_name) != normalize_text(emp.name):
                    warnings.append(
                        f"• {emp.name}: уже записан к другому водителю ({other_name})."
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

    # =========================
    # Shift consistency
    # =========================

    def enforce_shift_consistency(self, driver_tgid: int) -> list[str]:
        """Удалить пассажиров, чья смена не совпадает со сменой водителя.

        Запись водителя НЕ удаляется — удаляются только пассажиры с несовпадающей сменой.
        Возвращает список имён удалённых пассажиров.

        ОТКЛЮЧЕНО: сотрудники часто меняют смены, и авто-очистка удаляла ещё
        актуальных пассажиров раньше, чем админ успевал обновить данные.
        Чтобы вернуть прежнее поведение — убери early-return ниже.
        """
        return []

        driver = self.get_driver(driver_tgid)
        if not driver:
            return []

        driver_shift = ShiftType.from_string(driver.shift)
        # Fallback к employees если ARRAYFORMULA ещё не пересчиталась
        if driver_shift == ShiftType.UNKNOWN:
            emp_driver = self.get_employee_by_name(driver.name)
            if emp_driver:
                driver_shift = ShiftType.from_string(emp_driver.shift)
        if driver_shift == ShiftType.UNKNOWN:
            return []

        dp = self.get_driver_passengers(driver_tgid)
        if not dp or not dp.passengers:
            return []

        keep = []
        removed = []

        for pname in dp.passengers:
            emp = self.get_employee_by_name(pname)
            if not emp:
                # Сотрудник не найден — удаляем из списка
                removed.append(pname)
                continue
            p_shift = ShiftType.from_string(emp.shift)
            if p_shift != driver_shift:
                removed.append(pname)
            else:
                keep.append(pname)

        if not removed:
            return []

        dp.passengers = keep
        self.upsert_driver_passengers(dp)
        self.clear_rides_with(names=set(removed))

        logger.info(
            "enforce_shift_consistency: driver_tgid=%d removed=%r kept=%r",
            driver_tgid, removed, keep,
        )

        return removed
    # =========================
    # Cities (справочник для поиска водителя)
    # =========================

    def cities(self) -> list[tuple[str, str]]:
        values = self._values(self.config.CITIES_SHEET)
        if not values or len(values) < 2:
            return []
        col = self._col_map(values[0])
        c_city, c_state = col.get("City"), col.get("State")
        if c_city is None:
            return []
        out, seen = [], set()
        for row in values[1:]:
            city = (row[c_city] if c_city < len(row) else "").strip()
            state = (row[c_state] if c_state is not None and c_state < len(row) else "").strip()
            if not city:
                continue
            key = (normalize_text(city), normalize_text(state))
            if key in seen:
                continue
            seen.add(key)
            out.append((city, state))
        return out

    def states(self) -> list[str]:
        out, seen = [], set()
        for _, state in self.cities():
            if state and normalize_text(state) not in seen:
                seen.add(normalize_text(state))
                out.append(state)
        return out

    def resolve_city(self, raw: str) -> Optional[tuple[str, str]]:
        # Оставлено для обратной совместимости: одно совпадение или None.
        matches = self.find_cities(raw)
        return matches[0] if len(matches) == 1 else None

    def find_cities(self, raw: str) -> list[tuple[str, str]]:
        """Все совпадения по вводу.
        - «San Jose, CA» / «Springfield IL» → точный (город + штат).
        - «Springfield» (без штата) → все пары с этим городом (может быть >1 → уточнять штат).
        - нет совпадений → [].
        """
        n = normalize_text(raw)
        all_c = self.cities()
        # 1) ввод с штатом: последний токен — 2 буквы
        parts = n.replace(",", " ").split()
        if len(parts) >= 2 and len(parts[-1]) == 2:
            st = parts[-1]
            city_part = " ".join(parts[:-1])
            exact = [(c, s) for c, s in all_c
                     if normalize_text(c) == city_part and normalize_text(s) == st]
            if exact:
                return exact
        # 2) полное «city, state» как есть
        exact2 = [(c, s) for c, s in all_c if normalize_text(f"{c}, {s}") == n]
        if exact2:
            return exact2
        # 3) по названию города (может дать несколько штатов)
        return [(c, s) for c, s in all_c if normalize_text(c) == n]

    def state_of_city(self, city: str) -> str:
        n = normalize_text(city)
        for c, s in self.cities():
            if normalize_text(c) == n:
                return s
        return ""

    # =========================
    # Drivers: полный список и поиск по городу/штату
    # =========================

    def all_active_drivers(self) -> list[Driver]:
        values = self._values(self.config.DRIVERS_SHEET)
        if not values or len(values) < 2:
            return []
        headers = values[0]
        out = []
        for row in values[1:]:
            try:
                d = Driver.from_row(self._row_dict(headers, row))
            except Exception:
                continue
            if d.is_active and d.name:
                out.append(d)
        return out

    def find_drivers(self, *, city: str = "", state: str = "") -> list[Driver]:
        drivers = self.all_active_drivers()
        if state:
            n = normalize_text(state)
            drivers = [d for d in drivers if normalize_text(d.state) == n]
        elif city:
            n = normalize_text(city)
            drivers = [d for d in drivers if normalize_text(d.city) == n]
        else:
            return []
        return drivers

    def get_driver_by_name(self, name: str) -> Optional[Driver]:
        """Найти запись водителя по имени (нормализованно). Для проверки двойной роли."""
        n = normalize_text(name)
        values = self._values(self.config.DRIVERS_SHEET)
        if not values or len(values) < 2:
            return None
        headers = values[0]
        col = self._col_map(headers)
        name_col = col.get("Name")
        if name_col is None:
            return None
        for row in values[1:]:
            if name_col < len(row) and normalize_text(row[name_col]) == n:
                try:
                    return Driver.from_row(self._row_dict(headers, row))
                except Exception:
                    return None
        return None

    def driver_own_passenger_count(self, name: str) -> int:
        """Сколько СВОИХ пассажиров у водителя с этим именем (0, если не водитель)."""
        d = self.get_driver_by_name(name)
        if not d:
            return 0
        dp = self.get_driver_passengers(d.tg_id)
        return len(dp.passengers) if dp else 0

    def unlink_passenger_everywhere(self, passenger_name: str, *, except_tgid: int) -> Optional[tuple[int, str]]:
        """Убрать пассажира из карпула ЛЮБОГО другого водителя (кроме except_tgid).
        Возвращает (tgid, имя) прежнего водителя, у кого убрали, или None.
        Нужно для авто-отвязки: X «пересел за руль»."""
        hit = self.find_driver_for_passenger(passenger_name)
        if not hit:
            return None
        other_tgid, other_name = hit
        if int(other_tgid) == int(except_tgid):
            return None
        # не считаем self-assignment (X приписан к себе) за карпул
        if normalize_text(other_name) == normalize_text(passenger_name):
            return None
        dp = self.get_driver_passengers(other_tgid)
        if not dp:
            return None
        n = normalize_text(passenger_name)
        kept = [p for p in dp.passengers if normalize_text(p) != n]
        if len(kept) == len(dp.passengers):
            return None
        dp.passengers = kept
        self.upsert_driver_passengers(dp)
        self.clear_rides_with(names={passenger_name})
        return other_tgid, other_name
