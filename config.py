from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


class Buttons:
    BECOME_DRIVER = "🚗 Стать водителем"
    ADD_PASSENGERS = "👥 Добавить пассажиров"
    REMOVE_PASSENGER = "🧑‍🤝‍🧑 Удалить пассажира"
    MY_RECORD = "📋 Моя запись"
    STOP_BEING_DRIVER = "🛑 Перестать быть водителем"
    CANCEL = "↩️ Назад / Отмена"

    YES = "✅ Да"
    NO = "❌ Нет"

    ADMIN_WEEKLY_TARGET = "🎯 Проверка пассажиров (точечно)"
    ADMIN_MODE_TGID = "👤 По Telegram ID"
    ADMIN_MODE_SHIFT = "🕒 По смене сотрудников"
    SHIFT_DAY = "☀️ Дневная смена"
    SHIFT_NIGHT = "🌙 Ночная смена"


@dataclass
class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")
    GOOGLE_CREDENTIALS: str = os.getenv("GOOGLE_CREDENTIALS", "")

    DRIVERS_SHEET: str = os.getenv("DRIVERS_SHEET", "drivers")
    EMPLOYEES_SHEET: str = os.getenv("EMPLOYEES_SHEET", "employees")
    DRIVERS_PASSENGERS_SHEET: str = os.getenv("DRIVERS_PASSENGERS_SHEET", "drivers_passengers")

    STATE_FILE: str = os.getenv("STATE_FILE", "bot_state.json")
    CONFIRMATION_TIMEOUT_MINUTES: int = int(os.getenv("CONFIRMATION_TIMEOUT_MINUTES", "30"))

    ADMIN_USER_IDS: List[int] = None
    ADMIN_CHAT_ID: int = 0

    def __post_init__(self):
        raw_admins = os.getenv("ADMIN_USER_IDS", "").strip()
        if raw_admins:
            self.ADMIN_USER_IDS = [
                int(x.strip())
                for x in raw_admins.split(",")
                if x.strip().isdigit()
            ]
        else:
            self.ADMIN_USER_IDS = []

        raw_chat = os.getenv("ADMIN_CHAT_ID", "").strip()
        if raw_chat and raw_chat.lstrip("-").isdigit():
            self.ADMIN_CHAT_ID = int(raw_chat)
        else:
            self.ADMIN_CHAT_ID = self.ADMIN_USER_IDS[0] if self.ADMIN_USER_IDS else 0