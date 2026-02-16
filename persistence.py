# =========================
# STATE PERSISTENCE
# =========================

import json
import logging
from datetime import datetime
from typing import Dict, Optional, Any
from pathlib import Path
from threading import Lock


class StateManager:
    """Manages persistent state for the bot (pending confirmations, etc.)"""
    
    def __init__(self, state_file: str):
        self.state_file = Path(state_file)
        self.lock = Lock()
        self._state: Dict[str, Any] = {
            "pending_confirmations": {},  # tg_id -> {"shift_kind": "day/night", "timestamp": ISO}
            "last_weekly_check": {},      # "day" -> ISO timestamp, "night" -> ISO timestamp
        }
        self._load()
    
    def _load(self):
        """Load state from file"""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    loaded = json.load(f)
                    self._state.update(loaded)
                logging.info(f"State loaded from {self.state_file}")
        except Exception as e:
            logging.warning(f"Could not load state from {self.state_file}: {e}")
    
    def _save(self):
        """Save state to file"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self._state, f, indent=2)
            logging.debug(f"State saved to {self.state_file}")
        except Exception as e:
            logging.error(f"Could not save state to {self.state_file}: {e}")
    
    def add_pending_confirmation(self, tg_id: int, shift_kind: str):
        """Add a pending confirmation for a driver"""
        with self.lock:
            self._state["pending_confirmations"][str(tg_id)] = {
                "shift_kind": shift_kind,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self._save()
    
    def remove_pending_confirmation(self, tg_id: int) -> Optional[dict]:
        """Remove and return pending confirmation"""
        with self.lock:
            result = self._state["pending_confirmations"].pop(str(tg_id), None)
            self._save()
            return result
    
    def get_pending_confirmation(self, tg_id: int) -> Optional[dict]:
        """Get pending confirmation without removing"""
        return self._state["pending_confirmations"].get(str(tg_id))
    
    def has_pending_confirmation(self, tg_id: int) -> bool:
        """Check if driver has pending confirmation"""
        return str(tg_id) in self._state["pending_confirmations"]
    
    def get_all_pending_confirmations(self) -> Dict[str, dict]:
        """Get all pending confirmations"""
        return dict(self._state["pending_confirmations"])
    
    def update_last_weekly_check(self, shift_kind: str):
        """Update timestamp of last weekly check"""
        with self.lock:
            self._state["last_weekly_check"][shift_kind] = datetime.utcnow().isoformat()
            self._save()
    
    def get_last_weekly_check(self, shift_kind: str) -> Optional[str]:
        """Get timestamp of last weekly check"""
        return self._state["last_weekly_check"].get(shift_kind)
    
    def clear_all_pending_confirmations(self):
        """Clear all pending confirmations (use with caution)"""
        with self.lock:
            self._state["pending_confirmations"].clear()
            self._save()


# Global state manager instance (will be initialized in main)
state_manager: Optional[StateManager] = None


def init_state_manager(state_file: str):
    """Initialize the global state manager"""
    global state_manager
    state_manager = StateManager(state_file)
    return state_manager


def get_state_manager() -> StateManager:
    """Get the global state manager"""
    if state_manager is None:
        raise RuntimeError("State manager not initialized. Call init_state_manager() first.")
    return state_manager
