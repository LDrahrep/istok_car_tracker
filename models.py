# =========================
# DATA MODELS & UTILITIES
# =========================

from dataclasses import dataclass
from typing import Optional, List
from enum import Enum


def parse_int_safe(value, default: int = 0) -> int:
    """
    Safe int parser for Google Sheets cells.
    Handles '', None, whitespace; does not throw.
    """
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        return default


class ShiftType(Enum):
    """Shift types"""
    DAY = "day"
    NIGHT = "night"
    UNKNOWN = ""
    
    @classmethod
    def from_string(cls, value: str) -> 'ShiftType':
        """Parse shift from string (supports Russian and English)"""
        if not value:
            return cls.UNKNOWN
            
        value = value.strip().lower()
        
        if "night" in value or "ноч" in value:
            return cls.NIGHT
        elif "day" in value or "дн" in value:
            return cls.DAY
        else:
            return cls.UNKNOWN
    
    def to_display(self) -> str:
        """Convert to display string"""
        if self == ShiftType.DAY:
            return "Day"
        elif self == ShiftType.NIGHT:
            return "Night"
        else:
            return ""


@dataclass
class Driver:
    """Driver data model"""
    name: str
    tg_id: int
    phone: str
    shift: ShiftType
    car: str
    plates: str
    is_active: bool = True
    row_index: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: dict, row_index: Optional[int] = None) -> 'Driver':
        """Create Driver from sheet row dict"""
        return cls(
            name=data.get("Name", "").strip(),
            tg_id=parse_int_safe(data.get("telegramID"), default=0),
            # Your sheet uses "Phone Number" (capital N), but keep fallback for old header too:
            phone=(data.get("Phone Number", "") or data.get("Phone number", "")).strip(),
            shift=ShiftType.from_string(data.get("Shift", "")),
            car=data.get("Car", "").strip(),
            plates=data.get("Plates", "").strip(),
            is_active=str(data.get("isActive", "TRUE")).upper() == "TRUE",
            row_index=row_index,
        )


@dataclass
class Employee:
    """Employee data model"""
    name: str
    phone: str
    shift: ShiftType
    rides_with: str
    driver_tgid: Optional[int]
    row_index: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: dict, row_index: Optional[int] = None) -> 'Employee':
        """Create Employee from sheet row dict"""
        # Your screenshot shows employees sheet uses "telegramID"
        driver_tgid_raw = data.get("telegramID", "")
        driver_tgid = parse_int_safe(driver_tgid_raw, default=0) or None

        return cls(
            name=data.get("Employee", "").strip(),
            phone=(data.get("Phone Number", "") or data.get("PhoneNumber", "")).strip(),
            shift=ShiftType.from_string(data.get("Shift", "")),
            rides_with=data.get("Rides with", "").strip(),
            driver_tgid=driver_tgid,
            row_index=row_index,
        )


@dataclass
class DriverPassengers:
    """Driver with passengers data model"""
    driver_name: str
    driver_tgid: int
    phone: str
    shift: ShiftType
    passengers: List[str]
    row_index: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: dict, row_index: Optional[int] = None) -> 'DriverPassengers':
        """Create DriverPassengers from sheet row dict"""
        passengers = [
            data.get("Passenger1", "").strip(),
            data.get("Passenger2", "").strip(),
            data.get("Passenger3", "").strip(),
            data.get("Passenger4", "").strip(),
        ]
        passengers = [p for p in passengers if p]
        
        # Your drivers_passengers sheet uses telegramID, not TGID
        return cls(
            driver_name=data.get("Name", "").strip(),
            driver_tgid=parse_int_safe(data.get("telegramID"), default=0),
            phone=(data.get("Phone Number", "") or data.get("Phone number", "")).strip(),
            shift=ShiftType.from_string(data.get("Shift", "")),
            passengers=passengers,
            row_index=row_index,
        )


def normalize_text(text: str) -> str:
    """Normalize text for comparison (lowercase, strip)"""
    return (text or "").strip().lower()


class BotError(Exception):
    """Base exception for bot errors"""
    pass


class SheetError(BotError):
    """Sheet operation error"""
    pass


class ValidationError(BotError):
    """Data validation error"""
    pass