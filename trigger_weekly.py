import os
import sys
import urllib.request
import urllib.parse

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("day", "night"):
        print("Usage: python trigger_weekly.py day|night")
        sys.exit(2)

    shift = sys.argv[1]
    base_url = os.environ.get("BOT_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("WEEKLY_TRIGGER_TOKEN", "").strip()

    if not base_url:
        print("Missing BOT_BASE_URL env var, e.g. https://your-bot.up.railway.app")
        sys.exit(2)
    if not token:
        print("Missing WEEKLY_TRIGGER_TOKEN env var")
        sys.exit(2)

    qs = urllib.parse.urlencode({"shift": shift, "token": token})
    url = f"{base_url}/weekly?{qs}"

    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"HTTP {resp.status}: {body}")

if __name__ == "__main__":
    main()