# =========================
# CONFIGURATION
# =========================

import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo


@dataclass
class BotConfig:
    """Bot configuration with all constants"""
    
    # Timezone and shift times
    TIMEZONE: str = "America/Chicago"
    DAY_SHIFT_TIME: time = None
    NIGHT_SHIFT_TIME: time = None
    
    # Limits
    MAX_PASSENGERS: int = 4
    CONFIRMATION_TIMEOUT_MINUTES: int = 60
    MAX_RETRIES: int = 3
    RETRY_DELAY_SECONDS: int = 2
    
    # Google Sheets
    SPREADSHEET_ID: str = None
    DRIVERS_SHEET: str = "drivers"
    EMPLOYEES_SHEET: str = "employees"
    DRIVERS_PASSENGERS_SHEET: str = "drivers_passengers"
    
    # Telegram
    BOT_TOKEN: str = None
    ADMIN_USERS: set = None
    
    # Persistence
    STATE_FILE: str = "bot_state.json"
    
    # Sheet column indices (for reference)
    EMPLOYEES_COLS = {
        "EMPLOYEE": 0,      # A
        "PHONE": 1,         # B
        "SHIFT": 2,         # C
        "RIDES_WITH": 3,    # D
        "DRIVER_TGID": 4,   # E
    }
    
    DRIVERS_COLS = {
        "NAME": 0,          # A
        "TGID": 1,          # B
        "PHONE": 2,         # C
        "SHIFT": 3,         # D
        "CAR": 4,           # E
        "PLATES": 5,        # F
        "IS_ACTIVE": 6,     # G
    }
    
    DRIVERS_PASSENGERS_COLS = {
        "NAME": 0,          # A
        "TGID": 1,          # B
        "PHONE": 2,         # C
        "SHIFT": 3,         # D
        "PASSENGER1": 4,    # E
        "PASSENGER2": 5,    # F
        "PASSENGER3": 6,    # G
        "PASSENGER4": 7,    # H
    }
    
    def __post_init__(self):
        """Parse time strings after initialization"""
        if isinstance(self.DAY_SHIFT_TIME, str):
            self.DAY_SHIFT_TIME = self._parse_time(self.DAY_SHIFT_TIME)
        if isinstance(self.NIGHT_SHIFT_TIME, str):
            self.NIGHT_SHIFT_TIME = self._parse_time(self.NIGHT_SHIFT_TIME)
    
    def _parse_time(self, hhmm: str) -> time:
        """Parse time string to time object with timezone"""
        h, m = hhmm.split(":")
        return time(int(h), int(m), tzinfo=ZoneInfo(self.TIMEZONE))


def load_config() -> BotConfig:
    """Load configuration from environment variables"""

    # Validate required environment variables
    required_vars = {
        "TELEGRAM_BOT_TOKEN": "Telegram Bot Token",
        "SPREADSHEET_ID": "Google Spreadsheet ID",
    }

    missing = []
    for var, description in required_vars.items():
        if not os.environ.get(var):
            missing.append(f"{var} ({description})")

    if missing:
        raise ValueError(
            "Missing required environment variables:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    # Parse admin users from comma-separated string
    admin_ids_str = os.environ.get("ADMIN_USER_IDS", "1270793968")
    admin_users = set(int(uid.strip()) for uid in admin_ids_str.split(",") if uid.strip())

    return BotConfig(
        TIMEZONE="America/Chicago",
        DAY_SHIFT_TIME="07:00",
        NIGHT_SHIFT_TIME="19:00",
        SPREADSHEET_ID=os.environ["SPREADSHEET_ID"],
        BOT_TOKEN=os.environ["TELEGRAM_BOT_TOKEN"],
        ADMIN_USERS=admin_users,
        STATE_FILE=os.environ.get("STATE_FILE", "bot_state.json"),
    )


# Button labels (Russian)
class Buttons:
    ADD = "🚗 Добавить/обновить водителя"
    PASS = "👥 Указать пассажиров"
    DEL = "🗑 Удалить пассажира"
    MY = "📄 Моя запись"
    CANCEL = "❌ Отмена"
    SHUTDOWN = "🛑 Shutdown"
    FORCE_WEEKLY = "📢 Запустить weekly-проверку"
    
    YES = "Да"
    NO = "Нет"
    
    DAY = "Day"
    NIGHT = "Night"
