import os
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

def call(url: str):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"HTTP {resp.status}: {body}")

def main():
    base_url = os.environ.get("BOT_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("WEEKLY_TRIGGER_TOKEN", "").strip()
    window_min = int(os.environ.get("TRIGGER_WINDOW_MINUTES", "6"))

    if not base_url:
        raise SystemExit("Missing BOT_BASE_URL env var, e.g. https://your-bot.up.railway.app")
    if not token:
        raise SystemExit("Missing WEEKLY_TRIGGER_TOKEN env var")

    # cron runs in UTC, but we decide by Chicago local time (handles DST automatically)
    tz = ZoneInfo("America/Chicago")
    now = datetime.now(tz)

    # Only on Sunday (Python weekday: Mon=0 ... Sun=6)
    if now.weekday() != 6:
        print(f"Skip: not Sunday. now={now.isoformat()}")
        return

    def in_window(target_h: int, target_m: int) -> bool:
        minutes_now = now.hour * 60 + now.minute
        minutes_target = target_h * 60 + target_m
        return 0 <= (minutes_now - minutes_target) < window_min

    # At/after 07:00 (Sunday)
    if in_window(7, 0):
        qs = urllib.parse.urlencode({"shift": "day", "token": token})
        url = f"{base_url}/weekly?{qs}"
        print(f"Trigger DAY: {url}")
        call(url)
        return

    # At/after 19:00 (Sunday)
    if in_window(19, 0):
        qs = urllib.parse.urlencode({"shift": "night", "token": token})
        url = f"{base_url}/weekly?{qs}"
        print(f"Trigger NIGHT: {url}")
        call(url)
        return

    print(f"Skip: not in trigger window. now={now.isoformat()}")

if __name__ == "__main__":
    main()