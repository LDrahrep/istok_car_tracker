# =========================
# DATA MODELS & UTILITIES
# =========================

from dataclasses import dataclass
from typing import Optional, List
from enum import Enum


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
            tg_id=int(data.get("telegramID", 0)),
            phone=data.get("Phone number", "").strip(),
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
        driver_tgid = data.get("Driver's TGID", "")
        return cls(
            name=data.get("Employee", "").strip(),
            phone=data.get("PhoneNumber", "").strip(),
            shift=ShiftType.from_string(data.get("Shift", "")),
            rides_with=data.get("Rides with", "").strip(),
            driver_tgid=int(driver_tgid) if driver_tgid and str(driver_tgid).strip() else None,
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
        
        return cls(
            driver_name=data.get("Name", "").strip(),
            driver_tgid=int(data.get("TGID", 0)),
            phone=data.get("Phone Number", "").strip(),
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
