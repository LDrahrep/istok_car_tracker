from __future__ import annotations

from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from models import (
    Driver,
    DriverPassengers,
    Employee,
    ShiftType,
    SheetError,
    normalize_text,
)


class SheetManager:
    def __init__(self, config):
        self.config = config
        self.client = self._build_client()

    # -------------------------
    # Google client
    # -------------------------

    def _build_client(self):
        import json

        info = json.loads(self.config.GOOGLE_CREDENTIALS)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        return gspread.authorize(creds)

    def _ws(self, name: str):
        sh = self.client.open_by_key(self.config.SPREADSHEET_ID)
        return sh.worksheet(name)

    def _values(self, name: str):
        # Всегда читаем напрямую из Google Sheets (без кэша)
        ws = self._ws(name)
        return ws.get_all_values()

    def _invalidate(self, name: str):
        # кэш отключён — ничего делать не нужно
        return

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

    def upsert_driver(self, driver: Driver):
        values = self._values(self.config.DRIVERS_SHEET)
        if not values:
            raise SheetError("drivers sheet empty")

        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
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
                col_letter = chr(ord('A') + idx)
                updates.append({"range": f"{col_letter}{existing}", "values": [[value]]})

            put("Name", driver.name)
            # telegramID обязателен
            col_letter = chr(ord('A') + tg_col)
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
                col_letter = chr(ord('A') + idx)
                updates.append({"range": f"{col_letter}{existing}", "values": [[value]]})

            put("Name", dp.driver_name)
            col_letter = chr(ord('A') + tg_col)
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
        """Очистить employees.rides_with для заданных сотрудников (по TGID и/или имени).

        Возвращает число обновлённых строк.
        """
        tg_ids = tg_ids or set()
        names_norm = {normalize_text(n) for n in (names or set()) if n}

        values = self._values(self.config.EMPLOYEES_SHEET)
        if not values or len(values) < 2:
            return 0

        headers = values[0]
        col = self._col_map(headers)

        # В таблице встречаются разные варианты заголовков
        tg_col = col.get("telegramID") or col.get("telegramid")
        name_col = col.get("Employee") or col.get("Name")
        rides_col = col.get("Rides with")

        if rides_col is None:
            return 0

        ws = self._ws(self.config.EMPLOYEES_SHEET)
        updates = []

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

            # Ставим пустое значение в rides_with
            col_letter = chr(ord('A') + rides_col)
            updates.append({"range": f"{col_letter}{idx}", "values": [[""]]})

        if not updates:
            return 0

        # batch_update устойчивее и быстрее
        ws.batch_update(updates)
        self._invalidate(self.config.EMPLOYEES_SHEET)
        return len(updates)

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

        errors: list[str] = []
        warnings: list[str] = []
        valid: list[Employee] = []

        driver_emp = self.get_employee_by_tgid(driver_tgid)
        driver_shift = self.get_shift_for_tgid(driver_tgid)

        if driver_shift == ShiftType.UNKNOWN:
            errors.append(
                "⛔ У тебя не указана смена в списке сотрудников (employees → Shift).\n"
                "Попроси обновить смену или напиши администратору."
            )
            return [], errors, warnings

        driver_name_norm = normalize_text(driver_emp.name) if driver_emp else ""

        employees = {
            normalize_text(e.name): e
            for e in self.get_all_employees()
            if e.name
        }

        seen = set()

        for raw in names:
            n = normalize_text(raw)
            if not n or n in seen:
                continue

            seen.add(n)
            emp = employees.get(n)

            if not emp:
                warnings.append(
                    f"😕 Не найден сотрудник: {raw} — пропущен.\n"
                    "Проверь написание (как в employees)."
                )
                continue

            # запрет водитель = пассажир
            if (
                (emp.tg_id and int(emp.tg_id) == int(driver_tgid))
                or n == driver_name_norm
            ):
                warnings.append(
                    "🙃 Водитель не может быть пассажиром — этот пункт пропущен.\n"
                    "Если ты больше не водитель, нажми «🛑 Перестать быть водителем», "
                    "и тогда тебя смогут добавить пассажиром."
                )
                continue

            p_shift = ShiftType.from_string(emp.shift)

            if p_shift != driver_shift:
                warnings.append(
                    f"⏰ {emp.name} в другой смене — пропущен."
                )
                continue

            valid.append(emp)

        if len(valid) > 4:
            warnings.append("⚠️ Максимум 4 пассажира — лишние будут проигнорированы.")
            valid = valid[:4]

        if not valid:
            errors.append(
                "⛔ Не получилось добавить пассажиров: после проверок список пуст.\n"
                "Проверь имена и смену сотрудников."
            )

        return valid, errors, warnings