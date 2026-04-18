from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


def normalize_text(s: str) -> str:
    s = (s or "").replace('\u00a0', ' ').replace('\u200b', '').replace('\ufeff', '')
    return " ".join(s.split()).casefold()


def normalize_sorted(s: str) -> str:
    return " ".join(sorted(normalize_text(s).split()))


def normalize_shift(raw: str) -> str:
    s = normalize_text(raw)

    if s in ("day",):
        return "day"

    if s in ("night",):
        return "night"

    if s in ("meltech day",):
        return "meltech_day"

    if s in ("meltech night",):
        return "meltech_night"

    # Обратная совместимость: старое "Meltech" (без Day/Night) → meltech_day
    if s in ("meltech",):
        return "meltech_day"

    if not s:
        return "unknown"

    return "other"


class ShiftType(Enum):
    DAY = "day"
    NIGHT = "night"
    MELTECH_DAY = "meltech_day"
    MELTECH_NIGHT = "meltech_night"
    UNKNOWN = "unknown"
    OTHER = "other"

    @staticmethod
    def from_string(raw: str) -> "ShiftType":
        s = normalize_shift(raw)
        for member in ShiftType:
            if member.value == s:
                return member
        return ShiftType.OTHER

    def to_display(self) -> str:
        if self == ShiftType.DAY:
            return "Day"
        if self == ShiftType.NIGHT:
            return "Night"
        if self == ShiftType.MELTECH_DAY:
            return "Meltech Day"
        if self == ShiftType.MELTECH_NIGHT:
            return "Meltech Night"
        if self == ShiftType.UNKNOWN:
            return "Unknown"
        return "Other"


class SheetError(Exception):
    pass


class ValidationError(Exception):
    pass


@dataclass
class Employee:
    name: str
    phone: str = ""
    shift: str = "unknown"
    rides_with: str = ""
    tg_id: Optional[int] = None

    @staticmethod
    def from_row(row: dict) -> "Employee":
        tg_raw = (row.get("telegramID") or row.get("telegramid") or "").strip()
        tg_id = int(tg_raw) if tg_raw.isdigit() else None

        # Колонка с именем может называться "Employee", "Name",
        # или вообще не иметь заголовка (пустая строка "").
        name = (
            row.get("Employee")
            or row.get("Name")
            or row.get("")
            or ""
        )

        return Employee(
            name=name.strip() if name else "",
            phone=row.get("Phone Number") or "",
            shift=row.get("Shift") or "",
            rides_with=row.get("Rides with") or "",
            tg_id=tg_id,
        )


@dataclass
class Driver:
    name: str
    tg_id: int
    car: str = ""
    plates: str = ""
    shift: str = ""
    is_active: bool = True

    @staticmethod
    def from_row(row: dict) -> "Driver":
        tg_raw = (row.get("telegramID") or "").strip()
        if not tg_raw.isdigit():
            raise ValidationError("Driver telegramID missing")

        is_active_raw = str(row.get("isActive") or "TRUE").strip().casefold()

        return Driver(
            name=row.get("Name") or "",
            tg_id=int(tg_raw),
            car=row.get("Car") or "",
            plates=row.get("Plates") or "",
            shift=row.get("Shift") or "",
            is_active=is_active_raw != "false",
        )


@dataclass
class DriverPassengers:
    driver_name: str
    driver_tgid: int
    passengers: list[str]
    shift_raw: str = ""

    @staticmethod
    def from_row(row: dict) -> "DriverPassengers":
        tg_raw = (row.get("telegramID") or "").strip()
        if not tg_raw.isdigit():
            raise ValidationError("DriverPassengers telegramID missing")

        passengers = []
        for key in ("Passenger1", "Passenger2", "Passenger3", "Passenger4"):
            val = (row.get(key) or "").strip()
            if val:
                passengers.append(val)

        return DriverPassengers(
            driver_name=row.get("Name") or "",
            driver_tgid=int(tg_raw),
            passengers=passengers,
            shift_raw=row.get("Shift") or "",
        )