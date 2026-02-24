from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class BotState:
    pending_confirmations: Dict[str, dict] = field(default_factory=dict)


class StateManager:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self._lock = threading.Lock()
        self.state = BotState()
        self._load()

    def _load(self):
        if not os.path.exists(self.filepath):
            return

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.state.pending_confirmations = (
                data.get("pending_confirmations", {}) or {}
            )
        except Exception:
            self.state = BotState()

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pending_confirmations": self.state.pending_confirmations
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def is_pending(self, tg_id: int) -> bool:
        return str(tg_id) in self.state.pending_confirmations

    def add_pending(self, tg_id: int, shift: str):
        with self._lock:
            self.state.pending_confirmations[str(tg_id)] = {
                "shift": shift
            }
            self._save()

    def remove_pending(self, tg_id: int):
        with self._lock:
            self.state.pending_confirmations.pop(str(tg_id), None)
            self._save()


_state_mgr: Optional[StateManager] = None


def get_state_manager(path: str = "bot_state.json") -> StateManager:
    global _state_mgr

    if _state_mgr is None:
        _state_mgr = StateManager(path)

    return _state_mgr