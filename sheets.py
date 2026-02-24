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
        self._cache = {}

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
        if name in self._cache:
            return self._cache[name]

        ws = self._ws(name)
        vals = ws.get_all_values()
        self._cache[name] = vals
        return vals

    def _invalidate(self, name: str):
        self._cache.pop(name, None)

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
            if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
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
            if tg_col < len(row) and str(row[tg_col]).strip() == str(driver.tg_id):
                existing = i
                break

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

        if existing:
            rng = f"A{existing}:{chr(ord('A') + len(headers) - 1)}{existing}"
            ws.update(rng, [row_out])
        else:
            ws.append_row(row_out, value_input_option="USER_ENTERED")

        self._invalidate(self.config.DRIVERS_SHEET)

    def delete_driver(self, tg_id: int):
        values = self._values(self.config.DRIVERS_SHEET)
        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        ws = self._ws(self.config.DRIVERS_SHEET)

        for i, row in enumerate(values[1:], start=2):
            if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
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
            if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
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
            if tg_col < len(row) and str(row[tg_col]).strip() == str(dp.driver_tgid):
                existing = i
                break

        row_out = [""] * len(headers)

        if "Name" in col:
            row_out[col["Name"]] = dp.driver_name

        row_out[tg_col] = str(dp.driver_tgid)

        for idx, key in enumerate(
            ("Passenger1", "Passenger2", "Passenger3", "Passenger4")
        ):
            if key in col:
                row_out[col[key]] = (
                    dp.passengers[idx] if idx < len(dp.passengers) else ""
                )

        if existing:
            rng = f"A{existing}:{chr(ord('A') + len(headers) - 1)}{existing}"
            ws.update(rng, [row_out])
        else:
            ws.append_row(row_out, value_input_option="USER_ENTERED")

        self._invalidate(self.config.DRIVERS_PASSENGERS_SHEET)

    # =========================
    # Validation
    # =========================

    def validate_passengers(
        self, driver_tgid: int, names: list[str]
    ) -> tuple[list[Employee], list[str]]:

        errors = []
        valid = []

        driver_emp = self.get_employee_by_tgid(driver_tgid)
        driver_shift = self.get_shift_for_tgid(driver_tgid)

        if driver_shift == ShiftType.UNKNOWN:
            errors.append(
                "⛔ У тебя не указана смена в списке сотрудников."
            )
            return [], errors

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
                errors.append(f"😕 Не найден сотрудник: {raw}")
                continue

            # запрет водитель = пассажир
            if (
                (emp.tg_id and int(emp.tg_id) == int(driver_tgid))
                or n == driver_name_norm
            ):
                errors.append(
                    "🙃 Водитель не может быть пассажиром."
                )
                continue

            p_shift = ShiftType.from_string(emp.shift)

            if p_shift != driver_shift:
                errors.append(
                    f"⏰ {emp.name} в другой смене."
                )
                continue

            valid.append(emp)

        if len(valid) > 4:
            errors.append("⛔ Максимум 4 пассажира.")
            valid = valid[:4]

        return valid, errors