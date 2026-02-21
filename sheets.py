# =========================
# GOOGLE SHEETS OPERATIONS
# =========================
#
# FIX: Previously the cache stored only the worksheet *object*, meaning
# every method still called sheet.get_all_values() — a fresh API hit
# each time. With 6+ drivers registering simultaneously, a single
# "add passengers" action triggers 6+ reads (get_driver, get_all_employees
# x2, get_driver_passengers, get_employee_by_name x N), easily blowing
# the 60 reads/minute quota.
#
# Now _get_sheet_data() caches the actual row data. All read operations
# use the cache. Writes invalidate the cache so the next read is fresh.
# =========================

import json
import os
import time
import logging
import difflib
import threading
from typing import List, Optional, Tuple, Dict, Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, SpreadsheetNotFound

from models import Driver, Employee, DriverPassengers, ShiftType, normalize_text, SheetError, ValidationError
from config import BotConfig


class SheetManager:
    """Manages all Google Sheets operations with caching and error handling"""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    # How long (seconds) to keep sheet data in memory before re-fetching.
    # 60s is safe for a small-team bot; increase if you want fewer reads.
    DATA_CACHE_TTL = 60

    def __init__(self, config: BotConfig):
        self.config = config
        self.client = self._init_client()

        # Worksheet object cache (avoids re-opening the spreadsheet every time)
        self._ws_cache: Dict[str, Any] = {}

        # DATA cache: sheet_name -> {"data": [[...]], "ts": float}
        # This is the key fix — we cache the actual row data, not just the ws object.
        self._data_cache: Dict[str, Dict] = {}
        self._cache_lock = threading.Lock()

    # =========================
    # CLIENT INIT
    # =========================

    def _init_client(self) -> gspread.Client:
        """Initialize Google Sheets client"""
        try:
            info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
            creds = Credentials.from_service_account_info(info, scopes=self.SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logging.error(f"Failed to initialize Google Sheets client: {e}")
            raise SheetError(f"Could not connect to Google Sheets: {e}")

    # =========================
    # CORE CACHING LAYER
    # =========================

    def _get_worksheet(self, sheet_name: str):
        """Get worksheet object (cached; does NOT fetch data)."""
        if sheet_name not in self._ws_cache:
            try:
                ws = self.client.open_by_key(self.config.SPREADSHEET_ID).worksheet(sheet_name)
                self._ws_cache[sheet_name] = ws
            except (APIError, SpreadsheetNotFound) as e:
                raise SheetError(f"Could not open worksheet '{sheet_name}': {e}")
        return self._ws_cache[sheet_name]

    def _get_sheet_data(self, sheet_name: str) -> List[List[str]]:
        """
        Return all values for a sheet, using a TTL cache.

        This is the key method that prevents 429 errors.  Instead of
        hitting the API on every bot action, we serve cached data and
        only re-fetch when the TTL has expired or the cache was
        explicitly invalidated by a write.
        """
        with self._cache_lock:
            cached = self._data_cache.get(sheet_name)
            if cached and (time.time() - cached["ts"]) < self.DATA_CACHE_TTL:
                return cached["data"]

        # Cache miss — fetch with retry / back-off
        data = self._fetch_with_retry(sheet_name)

        with self._cache_lock:
            self._data_cache[sheet_name] = {"data": data, "ts": time.time()}

        return data

    def _fetch_with_retry(self, sheet_name: str) -> List[List[str]]:
        """Fetch sheet data with exponential back-off on 429 / transient errors."""
        last_exc = None
        for attempt in range(self.config.MAX_RETRIES):
            try:
                ws = self._get_worksheet(sheet_name)
                return ws.get_all_values()
            except APIError as e:
                last_exc = e
                error_info = e.args[0] if e.args and isinstance(e.args[0], dict) else {}
                code = error_info.get("code", 0)
                if code == 429:
                    # Rate-limited: use longer delays so the per-minute window resets
                    wait = min(60, 10 * (2 ** attempt))   # 10s, 20s, 40s, 60s
                else:
                    wait = self.config.RETRY_DELAY_SECONDS * (2 ** attempt)

                if attempt < self.config.MAX_RETRIES - 1:
                    logging.warning(
                        f"Sheet access failed (attempt {attempt + 1}), "
                        f"retrying in {wait}s: {e}"
                    )
                    time.sleep(wait)
                else:
                    logging.error(
                        f"Failed to access sheet {sheet_name} after "
                        f"{self.config.MAX_RETRIES} attempts"
                    )
            except SpreadsheetNotFound as e:
                raise SheetError(f"Spreadsheet not found: {e}")
            except Exception as e:
                last_exc = e
                if attempt < self.config.MAX_RETRIES - 1:
                    wait = self.config.RETRY_DELAY_SECONDS * (2 ** attempt)
                    logging.warning(f"Unexpected error (attempt {attempt + 1}), retrying in {wait}s: {e}")
                    time.sleep(wait)

        raise SheetError(f"Could not access sheet '{sheet_name}': {last_exc}")

    def _invalidate_cache(self, sheet_name: str):
        """Invalidate cached data for a sheet (call after any write)."""
        with self._cache_lock:
            self._data_cache.pop(sheet_name, None)
        # Also evict the ws object so we re-open on next access
        # (handles token expiry on long-running bots)
        self._ws_cache.pop(sheet_name, None)

    def _build_column_map(self, headers: List[str]) -> Dict[str, int]:
        """Build a map of normalized column names to indices."""
        return {normalize_text(h): i for i, h in enumerate(headers)}

    # =========================
    # DRIVER OPERATIONS
    # =========================

    def get_driver(self, tg_id: int) -> Optional[Driver]:
        """Get driver by Telegram ID (uses cached data)."""
        try:
            values = self._get_sheet_data(self.config.DRIVERS_SHEET)

            if not values or len(values) < 2:
                return None

            headers = values[0]
            col_map = self._build_column_map(headers)
            tg_col = col_map.get("telegramid")
            if tg_col is None:
                logging.error("telegramID column not found in drivers sheet")
                return None

            for i, row in enumerate(values[1:], start=2):
                if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
                    row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                    return Driver.from_dict(row_dict, row_index=i)

            return None
        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error getting driver {tg_id}: {e}")
            raise SheetError(f"Could not retrieve driver: {e}")

    def upsert_driver(self, driver: Driver) -> Tuple[bool, int]:
        """
        Insert or update driver.
        Returns: (is_new, row_index)
        """
        try:
            # Use fresh data (or cached) to find existing row
            values = self._get_sheet_data(self.config.DRIVERS_SHEET)

            if not values:
                raise SheetError("Drivers sheet is empty (no headers)")

            headers = values[0]
            col_map = self._build_column_map(headers)

            existing_row = None
            for i, row in enumerate(values[1:], start=2):
                tg_col = col_map.get("telegramid")
                if tg_col is not None and tg_col < len(row) and row[tg_col] == str(driver.tg_id):
                    existing_row = i
                    break

            update_data = self._prepare_driver_row(driver, headers, col_map)
            sheet = self._get_worksheet(self.config.DRIVERS_SHEET)

            if existing_row:
                sheet.update(
                    f"A{existing_row}:{chr(ord('A') + len(headers) - 1)}{existing_row}",
                    [update_data],
                    value_input_option="USER_ENTERED",
                )
                self._invalidate_cache(self.config.DRIVERS_SHEET)
                return False, existing_row
            else:
                sheet.append_row(update_data, value_input_option="USER_ENTERED")
                self._invalidate_cache(self.config.DRIVERS_SHEET)
                return True, len(values) + 1

        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error upserting driver {driver.tg_id}: {e}")
            raise SheetError(f"Could not save driver: {e}")

    def _prepare_driver_row(self, driver: Driver, headers: List[str], col_map: Dict[str, int]) -> List[str]:
        """Prepare driver row data matching headers."""
        row = [""] * len(headers)
        updates = {
            "name": driver.name,
            "telegramid": str(driver.tg_id),
            "phone number": driver.phone,
            "shift": driver.shift.to_display(),
            "car": driver.car,
            "plates": driver.plates,
            "isactive": "TRUE" if driver.is_active else "FALSE",
        }
        for key, value in updates.items():
            col = col_map.get(key)
            if col is not None:
                row[col] = value
        return row

    # =========================
    # EMPLOYEE OPERATIONS
    # =========================

    def get_employee_by_name(self, name: str) -> Optional[Employee]:
        """Get employee by name (uses cached data)."""
        try:
            values = self._get_sheet_data(self.config.EMPLOYEES_SHEET)

            if not values or len(values) < 2:
                return None

            headers = values[0]
            name_norm = normalize_text(name)

            for i, row in enumerate(values[1:], start=2):
                if len(row) > 0 and normalize_text(row[0]) == name_norm:
                    row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                    return Employee.from_dict(row_dict, row_index=i)

            return None
        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error getting employee {name}: {e}")
            raise SheetError(f"Could not retrieve employee: {e}")

    def get_all_employees(self) -> List[Employee]:
        """Get all employees (uses cached data)."""
        try:
            values = self._get_sheet_data(self.config.EMPLOYEES_SHEET)

            if not values or len(values) < 2:
                return []

            headers = values[0]
            employees = []
            for i, row in enumerate(values[1:], start=2):
                row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                employees.append(Employee.from_dict(row_dict, row_index=i))
            return employees
        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error getting all employees: {e}")
            raise SheetError(f"Could not retrieve employees: {e}")

    def update_employee_driver(self, employee_name: str, driver_name: str, driver_tgid: int) -> dict:
        """Update employee's driver assignment (columns D and E only).
        Returns a dict with 'success' key and optional 'error'/'message' keys."""
        try:
            sheet = self._get_worksheet(self.config.EMPLOYEES_SHEET)
            employee = self.get_employee_by_name(employee_name)

            if not employee or not employee.row_index:
                logging.warning(f"Employee {employee_name} not found, creating new row")
                sheet.append_row([employee_name, "", "", driver_name, str(driver_tgid)])
                self._invalidate_cache(self.config.EMPLOYEES_SHEET)
                return {'success': True}

            updates = [
                {'range': f'D{employee.row_index}', 'values': [[driver_name]]},
                {'range': f'E{employee.row_index}', 'values': [[str(driver_tgid)]]},
            ]
            sheet.batch_update(updates, value_input_option='USER_ENTERED')
            self._invalidate_cache(self.config.EMPLOYEES_SHEET)
            return {'success': True}

        except Exception as e:
            error_info = e.args[0] if e.args and isinstance(e.args[0], dict) else {}

            if error_info.get('code') == 400:
                if 'protected cell' in error_info.get('message', '').lower():
                    logging.error(
                        f"Permission error: Sheet is protected. Cannot update employee {employee_name}: {error_info}"
                    )
                    return {
                        'success': False,
                        'error': 'sheet_protected',
                        'message': (
                            "Google Sheet has protected cells. Please contact admin to remove "
                            "protection from columns D and E in the \"employees\" sheet."
                        ),
                    }

            logging.error(f"Error updating employee driver for {employee_name}: {error_info}")
            return {'success': False, 'error': 'unknown', 'message': str(e)}

    def clear_employee_driver(self, employee_name: str, only_if_driver_tgid: Optional[int] = None):
        """Clear employee's driver assignment."""
        try:
            employee = self.get_employee_by_name(employee_name)

            if not employee or not employee.row_index:
                return

            if only_if_driver_tgid is not None and employee.driver_tgid != only_if_driver_tgid:
                return

            sheet = self._get_worksheet(self.config.EMPLOYEES_SHEET)
            updates = [
                {'range': f'D{employee.row_index}', 'values': [['']]},
                {'range': f'E{employee.row_index}', 'values': [['']]},
            ]
            sheet.batch_update(updates, value_input_option='USER_ENTERED')
            self._invalidate_cache(self.config.EMPLOYEES_SHEET)

        except Exception as e:
            logging.error(f"Error clearing employee driver for {employee_name}: {e}")
            raise SheetError(f"Could not clear employee driver: {e}")

    def find_similar_employee_names(self, name: str, cutoff: float = 0.6) -> List[str]:
        """Find similar employee names using fuzzy matching.

        NOTE: reuses get_all_employees() which hits the cache — no extra API call.
        """
        try:
            employees = self.get_all_employees()   # <-- served from cache
            all_names = [e.name for e in employees if e.name]
            name_norm = normalize_text(name)
            all_names_norm = [normalize_text(n) for n in all_names]

            matches = difflib.get_close_matches(name_norm, all_names_norm, n=3, cutoff=cutoff)
            norm_to_original = {normalize_text(n): n for n in all_names}
            return [norm_to_original[m] for m in matches if m in norm_to_original]

        except Exception as e:
            logging.error(f"Error finding similar names for {name}: {e}")
            return []

    # =========================
    # DRIVER-PASSENGERS OPERATIONS
    # =========================

    def get_driver_passengers(self, tg_id: int) -> Optional[DriverPassengers]:
        """Get driver's passengers record (uses cached data)."""
        try:
            values = self._get_sheet_data(self.config.DRIVERS_PASSENGERS_SHEET)

            if not values or len(values) < 2:
                return None

            headers = values[0]
            col_map = self._build_column_map(headers)
            tg_col = col_map.get("telegramid")
            if tg_col is None:
                return None

            for i, row in enumerate(values[1:], start=2):
                if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
                    row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                    return DriverPassengers.from_dict(row_dict, row_index=i)

            return None
        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error getting driver passengers for {tg_id}: {e}")
            raise SheetError(f"Could not retrieve driver passengers: {e}")

    def upsert_driver_passengers(self, dp: DriverPassengers) -> int:
        """Insert or update driver passengers record."""
        try:
            values = self._get_sheet_data(self.config.DRIVERS_PASSENGERS_SHEET)

            if not values:
                raise SheetError("Drivers passengers sheet is empty")

            headers = values[0]
            col_map = self._build_column_map(headers)

            existing_row = None
            for i, row in enumerate(values[1:], start=2):
                tg_col = col_map.get("telegramid")
                if tg_col is not None and tg_col < len(row) and row[tg_col] == str(dp.driver_tgid):
                    existing_row = i
                    break

            row_data = self._prepare_driver_passengers_row(dp, headers, col_map)
            sheet = self._get_worksheet(self.config.DRIVERS_PASSENGERS_SHEET)

            if existing_row:
                sheet.update(
                    f"A{existing_row}:{chr(ord('A') + len(headers) - 1)}{existing_row}",
                    [row_data],
                    value_input_option="USER_ENTERED",
                )
                self._invalidate_cache(self.config.DRIVERS_PASSENGERS_SHEET)
                return existing_row
            else:
                sheet.append_row(row_data, value_input_option="USER_ENTERED")
                self._invalidate_cache(self.config.DRIVERS_PASSENGERS_SHEET)
                return len(values) + 1

        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error upserting driver passengers for {dp.driver_tgid}: {e}")
            raise SheetError(f"Could not save driver passengers: {e}")

    def _prepare_driver_passengers_row(self, dp: DriverPassengers, headers: List[str], col_map: Dict[str, int]) -> List[str]:
        """Prepare driver passengers row data."""
        row = [""] * len(headers)
        passengers = dp.passengers + [""] * (4 - len(dp.passengers))
        updates = {
            "name": dp.driver_name,
            "telegramid": str(dp.driver_tgid),
            "phone number": dp.phone,
            "shift": dp.shift.to_display(),
            "passenger1": passengers[0],
            "passenger2": passengers[1],
            "passenger3": passengers[2],
            "passenger4": passengers[3],
        }
        for key, value in updates.items():
            col = col_map.get(key)
            if col is not None:
                row[col] = value
        return row

    def clear_driver_passengers(self, tg_id: int) -> List[str]:
        """Clear all passengers for a driver. Returns list of cleared passenger names."""
        try:
            dp = self.get_driver_passengers(tg_id)
            if not dp or not dp.row_index:
                return []

            cleared_passengers = list(dp.passengers)
            sheet = self._get_worksheet(self.config.DRIVERS_PASSENGERS_SHEET)

            updates = [
                {'range': f'E{dp.row_index}', 'values': [['']]},
                {'range': f'F{dp.row_index}', 'values': [['']]},
                {'range': f'G{dp.row_index}', 'values': [['']]},
                {'range': f'H{dp.row_index}', 'values': [['']]},
            ]
            sheet.batch_update(updates, value_input_option='USER_ENTERED')
            self._invalidate_cache(self.config.DRIVERS_PASSENGERS_SHEET)

            return cleared_passengers

        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error clearing driver passengers for {tg_id}: {e}")
            raise SheetError(f"Could not clear driver passengers: {e}")

    def remove_passenger(self, tg_id: int, passenger_name: str) -> bool:
        """Remove a specific passenger from driver's list. Returns True if removed."""
        try:
            dp = self.get_driver_passengers(tg_id)
            if not dp or not dp.row_index:
                return False

            passenger_norm = normalize_text(passenger_name)
            passenger_index = next(
                (i for i, p in enumerate(dp.passengers) if normalize_text(p) == passenger_norm),
                None
            )

            if passenger_index is None:
                return False

            dp.passengers = [p for i, p in enumerate(dp.passengers) if i != passenger_index]
            self.upsert_driver_passengers(dp)
            return True

        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error removing passenger {passenger_name} from driver {tg_id}: {e}")
            raise SheetError(f"Could not remove passenger: {e}")

    # =========================
    # VALIDATION
    # =========================

    def validate_passengers(self, driver_tgid: int, driver_shift: ShiftType,
                            passenger_names: List[str]) -> Tuple[List[Employee], List[str]]:
        """
        Validate that passengers can be assigned to driver.
        Returns: (valid_employees, error_messages)

        All reads here go through the cache — get_all_employees(),
        find_similar_employee_names(), and get_driver() all serve
        cached data, so this whole method costs 0 extra API calls
        when the cache is warm.
        """
        errors = []
        valid_employees = []

        try:
            all_employees = self.get_all_employees()   # cached
            employee_map = {normalize_text(e.name): e for e in all_employees if e.name}

            for name in passenger_names:
                name_norm = normalize_text(name)

                if name_norm not in employee_map:
                    similar = self.find_similar_employee_names(name)   # also cached
                    if similar:
                        suggestions = "\n".join([f"• {s}" for s in similar])
                        errors.append(f"Пассажир '{name}' не найден.\n\nВозможно, вы имели в виду:\n{suggestions}")
                    else:
                        errors.append(f"Пассажир '{name}' не найден в employees. Проверьте написание.")
                    continue

                employee = employee_map[name_norm]

                if driver_shift != ShiftType.UNKNOWN and employee.shift != ShiftType.UNKNOWN:
                    if driver_shift != employee.shift:
                        errors.append(
                            f"⚠️ Смена пассажира '{name}' ({employee.shift.to_display()}) "
                            f"не совпадает с вашей ({driver_shift.to_display()})"
                        )
                        continue

                if employee.driver_tgid and employee.driver_tgid != driver_tgid:
                    errors.append(f"⛔ Пассажир '{name}' уже закреплён за другим водителем.")
                    continue

                if employee.rides_with and normalize_text(employee.rides_with) != normalize_text(str(driver_tgid)):
                    driver = self.get_driver(driver_tgid)   # cached
                    if driver and normalize_text(employee.rides_with) != normalize_text(driver.name):
                        errors.append(f"⛔ Пассажир '{name}' уже закреплён за другим водителем.")
                        continue

                valid_employees.append(employee)

            return valid_employees, errors

        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error validating passengers: {e}")
            raise SheetError(f"Could not validate passengers: {e}")

    # =========================
    # WEEKLY CHECK OPERATIONS
    # =========================

    def get_drivers_for_shift(self, shift: ShiftType) -> List[Driver]:
        """Get all active drivers for a specific shift (uses cached data)."""
        try:
            values = self._get_sheet_data(self.config.DRIVERS_SHEET)

            if not values or len(values) < 2:
                return []

            headers = values[0]
            drivers = []

            for i, row in enumerate(values[1:], start=2):
                row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                driver = Driver.from_dict(row_dict, row_index=i)
                if driver.tg_id and driver.shift == shift:
                    drivers.append(driver)

            return drivers
        except SheetError:
            raise
        except Exception as e:
            logging.error(f"Error getting drivers for shift {shift}: {e}")
            raise SheetError(f"Could not get drivers: {e}")