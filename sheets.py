# =========================
# GOOGLE SHEETS OPERATIONS
# =========================

import json
import os
import time
import logging
import difflib
from typing import List, Optional, Tuple, Dict, Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, SpreadsheetNotFound

from models import Driver, Employee, DriverPassengers, ShiftType, normalize_text, SheetError, ValidationError
from config import BotConfig


class SheetManager:
    """Manages all Google Sheets operations with caching and error handling"""
    
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.client = self._init_client()
        self._cache = {}
        self._cache_timeout = 30  # seconds
        self._last_cache_update = {}
    
    def _init_client(self) -> gspread.Client:
        """Initialize Google Sheets client"""
        try:
            info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
            creds = Credentials.from_service_account_info(info, scopes=self.SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logging.error(f"Failed to initialize Google Sheets client: {e}")
            raise SheetError(f"Could not connect to Google Sheets: {e}")
    
    def _get_worksheet(self, sheet_name: str, use_cache: bool = True):
        """Get worksheet with caching and error handling"""
        cache_key = f"ws_{sheet_name}"
        
        # Check cache
        if use_cache and cache_key in self._cache:
            if time.time() - self._last_cache_update.get(cache_key, 0) < self._cache_timeout:
                return self._cache[cache_key]
        
        # Fetch with retry logic
        for attempt in range(self.config.MAX_RETRIES):
            try:
                ws = self.client.open_by_key(self.config.SPREADSHEET_ID).worksheet(sheet_name)
                self._cache[cache_key] = ws
                self._last_cache_update[cache_key] = time.time()
                return ws
            except (APIError, SpreadsheetNotFound) as e:
                if attempt < self.config.MAX_RETRIES - 1:
                    wait_time = self.config.RETRY_DELAY_SECONDS * (2 ** attempt)
                    logging.warning(f"Sheet access failed (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                else:
                    logging.error(f"Failed to access sheet {sheet_name} after {self.config.MAX_RETRIES} attempts")
                    raise SheetError(f"Could not access sheet '{sheet_name}': {e}")
    
    def _invalidate_cache(self, sheet_name: str):
        """Invalidate cache for a sheet"""
        cache_key = f"ws_{sheet_name}"
        self._cache.pop(cache_key, None)
        self._last_cache_update.pop(cache_key, None)
    
    # =========================
    # DRIVER OPERATIONS
    # =========================
    
    def get_driver(self, tg_id: int) -> Optional[Driver]:
        """Get driver by Telegram ID"""
        try:
            sheet = self._get_worksheet(self.config.DRIVERS_SHEET)
            data = sheet.get_all_records()
            
            for i, row in enumerate(data, start=2):
                if str(row.get("telegramID")) == str(tg_id):
                    return Driver.from_dict(row, row_index=i)
            
            return None
        except Exception as e:
            logging.error(f"Error getting driver {tg_id}: {e}")
            raise SheetError(f"Could not retrieve driver: {e}")
    
    def upsert_driver(self, driver: Driver) -> Tuple[bool, int]:
        """
        Insert or update driver.
        Returns: (is_new, row_index)
        """
        try:
            sheet = self._get_worksheet(self.config.DRIVERS_SHEET)
            values = sheet.get_all_values()
            
            if not values:
                raise SheetError("Drivers sheet is empty (no headers)")
            
            headers = values[0]
            col_map = self._build_column_map(headers)
            
            # Find existing row
            existing_row = None
            for i, row in enumerate(values[1:], start=2):
                tg_col = col_map.get("telegramid")
                if tg_col is not None and tg_col < len(row) and row[tg_col] == str(driver.tg_id):
                    existing_row = i
                    break
            
            # Prepare update data
            update_data = self._prepare_driver_row(driver, headers, col_map)
            
            if existing_row:
                # Update existing row
                sheet.update(f"A{existing_row}:{chr(ord('A') + len(headers) - 1)}{existing_row}", 
                           [update_data], 
                           value_input_option="USER_ENTERED")
                self._invalidate_cache(self.config.DRIVERS_SHEET)
                return False, existing_row
            else:
                # Append new row
                sheet.append_row(update_data, value_input_option="USER_ENTERED")
                self._invalidate_cache(self.config.DRIVERS_SHEET)
                return True, len(values) + 1
                
        except Exception as e:
            logging.error(f"Error upserting driver {driver.tg_id}: {e}")
            raise SheetError(f"Could not save driver: {e}")
    
    def _prepare_driver_row(self, driver: Driver, headers: List[str], col_map: Dict[str, int]) -> List[str]:
        """Prepare driver row data matching headers"""
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
        """Get employee by name"""
        try:
            sheet = self._get_worksheet(self.config.EMPLOYEES_SHEET)
            data = sheet.get_all_records()
            
            name_norm = normalize_text(name)
            for i, row in enumerate(data, start=2):
                if normalize_text(row.get("Employee", "")) == name_norm:
                    return Employee.from_dict(row, row_index=i)
            
            return None
        except Exception as e:
            logging.error(f"Error getting employee {name}: {e}")
            raise SheetError(f"Could not retrieve employee: {e}")
    
    def get_all_employees(self) -> List[Employee]:
        """Get all employees"""
        try:
            sheet = self._get_worksheet(self.config.EMPLOYEES_SHEET)
            data = sheet.get_all_records()
            return [Employee.from_dict(row, row_index=i) for i, row in enumerate(data, start=2)]
        except Exception as e:
            logging.error(f"Error getting all employees: {e}")
            raise SheetError(f"Could not retrieve employees: {e}")
    
    def update_employee_driver(self, employee_name: str, driver_name: str, driver_tgid: int):
        """Update employee's driver assignment (columns D and E only)"""
        try:
            sheet = self._get_worksheet(self.config.EMPLOYEES_SHEET)
            employee = self.get_employee_by_name(employee_name)
            
            if not employee or not employee.row_index:
                logging.warning(f"Employee {employee_name} not found, creating new row")
                # Create new employee row
                sheet.append_row([employee_name, "", "", driver_name, str(driver_tgid)])
                self._invalidate_cache(self.config.EMPLOYEES_SHEET)
                return
            
            # Batch update columns D and E
            updates = [
                {
                    'range': f'D{employee.row_index}',
                    'values': [[driver_name]]
                },
                {
                    'range': f'E{employee.row_index}',
                    'values': [[str(driver_tgid)]]
                }
            ]
            sheet.batch_update(updates, value_input_option='USER_ENTERED')
            self._invalidate_cache(self.config.EMPLOYEES_SHEET)
            
        except Exception as e:
            logging.error(f"Error updating employee driver for {employee_name}: {e}")
            raise SheetError(f"Could not update employee: {e}")
    
    def clear_employee_driver(self, employee_name: str, only_if_driver_tgid: Optional[int] = None):
        """Clear employee's driver assignment"""
        try:
            employee = self.get_employee_by_name(employee_name)
            
            if not employee or not employee.row_index:
                return
            
            # Only clear if it matches the specified driver (if provided)
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
        """Find similar employee names using fuzzy matching"""
        try:
            employees = self.get_all_employees()
            all_names = [e.name for e in employees if e.name]
            name_norm = normalize_text(name)
            all_names_norm = [normalize_text(n) for n in all_names]
            
            matches = difflib.get_close_matches(name_norm, all_names_norm, n=3, cutoff=cutoff)
            
            # Map back to original names
            norm_to_original = {normalize_text(n): n for n in all_names}
            return [norm_to_original[m] for m in matches if m in norm_to_original]
            
        except Exception as e:
            logging.error(f"Error finding similar names for {name}: {e}")
            return []
    
    # =========================
    # DRIVER-PASSENGERS OPERATIONS
    # =========================
    
    def get_driver_passengers(self, tg_id: int) -> Optional[DriverPassengers]:
        """Get driver's passengers record"""
        try:
            sheet = self._get_worksheet(self.config.DRIVERS_PASSENGERS_SHEET)
            data = sheet.get_all_records()
            
            for i, row in enumerate(data, start=2):
                if str(row.get("TGID")) == str(tg_id):
                    return DriverPassengers.from_dict(row, row_index=i)
            
            return None
        except Exception as e:
            logging.error(f"Error getting driver passengers for {tg_id}: {e}")
            raise SheetError(f"Could not retrieve driver passengers: {e}")
    
    def upsert_driver_passengers(self, dp: DriverPassengers) -> int:
        """Insert or update driver passengers record"""
        try:
            sheet = self._get_worksheet(self.config.DRIVERS_PASSENGERS_SHEET)
            values = sheet.get_all_values()
            
            if not values:
                raise SheetError("Drivers passengers sheet is empty")
            
            headers = values[0]
            col_map = self._build_column_map(headers)
            
            # Find existing row
            existing_row = None
            for i, row in enumerate(values[1:], start=2):
                tg_col = col_map.get("tgid")
                if tg_col is not None and tg_col < len(row) and row[tg_col] == str(dp.driver_tgid):
                    existing_row = i
                    break
            
            # Prepare row data
            row_data = self._prepare_driver_passengers_row(dp, headers, col_map)
            
            if existing_row:
                sheet.update(f"A{existing_row}:{chr(ord('A') + len(headers) - 1)}{existing_row}",
                           [row_data],
                           value_input_option="USER_ENTERED")
                self._invalidate_cache(self.config.DRIVERS_PASSENGERS_SHEET)
                return existing_row
            else:
                sheet.append_row(row_data, value_input_option="USER_ENTERED")
                self._invalidate_cache(self.config.DRIVERS_PASSENGERS_SHEET)
                return len(values) + 1
                
        except Exception as e:
            logging.error(f"Error upserting driver passengers for {dp.driver_tgid}: {e}")
            raise SheetError(f"Could not save driver passengers: {e}")
    
    def _prepare_driver_passengers_row(self, dp: DriverPassengers, headers: List[str], col_map: Dict[str, int]) -> List[str]:
        """Prepare driver passengers row data"""
        row = [""] * len(headers)
        
        # Pad passengers to 4
        passengers = dp.passengers + [""] * (4 - len(dp.passengers))
        
        updates = {
            "name": dp.driver_name,
            "tgid": str(dp.driver_tgid),
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
        """
        Clear all passengers for a driver.
        Returns: List of cleared passenger names
        """
        try:
            dp = self.get_driver_passengers(tg_id)
            if not dp or not dp.row_index:
                return []
            
            cleared_passengers = list(dp.passengers)
            
            sheet = self._get_worksheet(self.config.DRIVERS_PASSENGERS_SHEET)
            
            # Clear passenger columns E-H
            updates = [
                {'range': f'E{dp.row_index}', 'values': [['']]},
                {'range': f'F{dp.row_index}', 'values': [['']]},
                {'range': f'G{dp.row_index}', 'values': [['']]},
                {'range': f'H{dp.row_index}', 'values': [['']]},
            ]
            sheet.batch_update(updates, value_input_option='USER_ENTERED')
            self._invalidate_cache(self.config.DRIVERS_PASSENGERS_SHEET)
            
            return cleared_passengers
            
        except Exception as e:
            logging.error(f"Error clearing driver passengers for {tg_id}: {e}")
            raise SheetError(f"Could not clear driver passengers: {e}")
    
    def remove_passenger(self, tg_id: int, passenger_name: str) -> bool:
        """
        Remove a specific passenger from driver's list.
        Returns: True if removed, False if not found
        """
        try:
            dp = self.get_driver_passengers(tg_id)
            if not dp or not dp.row_index:
                return False
            
            passenger_norm = normalize_text(passenger_name)
            
            # Find the passenger
            passenger_index = None
            for i, p in enumerate(dp.passengers):
                if normalize_text(p) == passenger_norm:
                    passenger_index = i
                    break
            
            if passenger_index is None:
                return False
            
            # Update the passengers list
            new_passengers = [p for i, p in enumerate(dp.passengers) if i != passenger_index]
            dp.passengers = new_passengers
            
            # Save back
            self.upsert_driver_passengers(dp)
            
            return True
            
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
        """
        errors = []
        valid_employees = []
        
        try:
            all_employees = self.get_all_employees()
            employee_map = {normalize_text(e.name): e for e in all_employees if e.name}
            
            for name in passenger_names:
                name_norm = normalize_text(name)
                
                # Check if employee exists
                if name_norm not in employee_map:
                    similar = self.find_similar_employee_names(name)
                    if similar:
                        suggestions = "\n".join([f"• {s}" for s in similar])
                        errors.append(f"Пассажир '{name}' не найден.\n\nВозможно, вы имели в виду:\n{suggestions}")
                    else:
                        errors.append(f"Пассажир '{name}' не найден в employees. Проверьте написание.")
                    continue
                
                employee = employee_map[name_norm]
                
                # Check shift compatibility
                if driver_shift != ShiftType.UNKNOWN and employee.shift != ShiftType.UNKNOWN:
                    if driver_shift != employee.shift:
                        errors.append(f"⚠️ Смена пассажира '{name}' ({employee.shift.to_display()}) не совпадает с вашей ({driver_shift.to_display()})")
                        continue
                
                # Check if already assigned to another driver
                if employee.driver_tgid and employee.driver_tgid != driver_tgid:
                    errors.append(f"⛔ Пассажир '{name}' уже закреплён за другим водителем.")
                    continue
                
                if employee.rides_with and normalize_text(employee.rides_with) != normalize_text(str(driver_tgid)):
                    # Double-check it's not just the name vs tgid mismatch
                    driver = self.get_driver(driver_tgid)
                    if driver and normalize_text(employee.rides_with) != normalize_text(driver.name):
                        errors.append(f"⛔ Пассажир '{name}' уже закреплён за другим водителем.")
                        continue
                
                valid_employees.append(employee)
            
            return valid_employees, errors
            
        except Exception as e:
            logging.error(f"Error validating passengers: {e}")
            raise SheetError(f"Could not validate passengers: {e}")
    
    # =========================
    # WEEKLY CHECK OPERATIONS
    # =========================
    
    def get_drivers_for_shift(self, shift: ShiftType) -> List[Driver]:
        """Get all drivers for a specific shift"""
        try:
            sheet = self._get_worksheet(self.config.DRIVERS_SHEET)
            data = sheet.get_all_records()
            
            drivers = []
            for i, row in enumerate(data, start=2):
                driver = Driver.from_dict(row, row_index=i)
                if driver.tg_id and driver.shift == shift:
                    drivers.append(driver)
            
            return drivers
        except Exception as e:
            logging.error(f"Error getting drivers for shift {shift}: {e}")
            raise SheetError(f"Could not get drivers: {e}")
    
    # =========================
    # UTILITIES
    # =========================
    
    def _build_column_map(self, headers: List[str]) -> Dict[str, int]:
        """Build a map of normalized column names to indices"""
        return {normalize_text(h): i for i, h in enumerate(headers)}
