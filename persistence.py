from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


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
                "shift": shift,
                "sent_at": time.time(),
            }
            self._save()

    def remove_pending(self, tg_id: int):
        with self._lock:
            self.state.pending_confirmations.pop(str(tg_id), None)
            self._save()

    def get_expired(self, timeout_seconds: int) -> List[Tuple[int, str]]:
        """Return list of (tg_id, shift) pairs that have been pending longer than timeout."""
        now = time.time()
        expired = []
        with self._lock:
            for key, entry in self.state.pending_confirmations.items():
                sent_at = entry.get("sent_at")
                if sent_at is None:
                    # Legacy entries without timestamp — treat as expired
                    expired.append((int(key), entry.get("shift", "")))
                    continue
                if now - sent_at >= timeout_seconds:
                    expired.append((int(key), entry.get("shift", "")))
        return expired


_state_mgr: Optional[StateManager] = None


def get_state_manager(path: str = "bot_state.json") -> StateManager:
    global _state_mgr

    if _state_mgr is None:
        _state_mgr = StateManager(path)

    return _state_mgr