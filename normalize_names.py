"""
One-time script to normalize names across all spreadsheet sheets.

Source of truth: `employees` sheet (Employee column, after TRIM).
Matching strategy:
  - Drivers: matched by telegramID
  - Passengers: matched by normalize_text (casefold + strip)

Usage:
  python normalize_names.py              # dry-run (only prints changes)
  python normalize_names.py --apply      # applies changes to the spreadsheet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Load .env if present (for local runs)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import gspread
from google.oauth2.service_account import Credentials

from config import Config
from models import normalize_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def col_letter(idx: int) -> str:
    """0-based column index -> A1 notation (A, B, ..., Z, AA, AB, ...)."""
    result = ""
    while True:
        result = chr(ord("A") + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def connect(config: Config):
    info = json.loads(config.GOOGLE_CREDENTIALS)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(config.SPREADSHEET_ID)


def col_map(headers: list[str]) -> dict[str, int]:
    return {h.strip(): i for i, h in enumerate(headers)}


# ---------------------------------------------------------------------------
# Build canonical maps from employees
# ---------------------------------------------------------------------------

def build_maps(spreadsheet, config: Config):
    """Read employees sheet and build:
      tgid_to_driver: {int(telegramID) -> canonical_name}   (only drivers)
      norm_to_canonical: {normalize_text(name) -> canonical_name} (all employees)
    """
    ws = spreadsheet.worksheet(config.EMPLOYEES_SHEET)
    values = ws.get_all_values()
    headers = values[0]
    col = col_map(headers)

    emp_col = col.get("Employee")
    tg_col = col.get("telegramID")
    rw_col = col.get("Rides with")

    if emp_col is None:
        print("ERROR: 'Employee' column not found in employees sheet")
        sys.exit(1)

    tgid_to_driver: dict[int, str] = {}
    norm_to_canonical: dict[str, str] = {}

    for row in values[1:]:
        if emp_col >= len(row):
            continue
        raw_name = row[emp_col]
        canonical = raw_name.strip()
        if not canonical:
            continue

        n = normalize_text(canonical)
        norm_to_canonical[n] = canonical

        # Is this employee a driver? (rides_with == own name AND has telegramID)
        rides_with = ""
        if rw_col is not None and rw_col < len(row):
            rides_with = row[rw_col].strip()

        tg_raw = ""
        if tg_col is not None and tg_col < len(row):
            tg_raw = row[tg_col].strip()

        if tg_raw.isdigit() and normalize_text(rides_with) == n:
            tgid_to_driver[int(tg_raw)] = canonical

    print(f"  employees: {len(norm_to_canonical)} names, {len(tgid_to_driver)} drivers")
    return tgid_to_driver, norm_to_canonical


# ---------------------------------------------------------------------------
# Sheet processors — each returns list of {range, values} updates
# ---------------------------------------------------------------------------

def fix_employees(spreadsheet, config, norm_to_canonical):
    """TRIM Employee column (remove leading/trailing spaces)."""
    ws = spreadsheet.worksheet(config.EMPLOYEES_SHEET)
    values = ws.get_all_values()
    headers = values[0]
    col = col_map(headers)
    emp_col = col.get("Employee")
    if emp_col is None:
        return []

    updates = []
    letter = col_letter(emp_col)

    for i, row in enumerate(values[1:], start=2):
        if emp_col >= len(row):
            continue
        raw = row[emp_col]
        trimmed = raw.strip()
        if raw != trimmed and trimmed:
            updates.append({
                "range": f"{letter}{i}",
                "values": [[trimmed]],
            })
            print(f"    [{config.EMPLOYEES_SHEET}] row {i}: {raw!r} -> {trimmed!r}")

    return updates


def fix_drivers(spreadsheet, config, tgid_to_driver):
    """Fix Name column using telegramID lookup."""
    ws = spreadsheet.worksheet(config.DRIVERS_SHEET)
    values = ws.get_all_values()
    headers = values[0]
    col = col_map(headers)

    name_col = col.get("Name")
    tg_col = col.get("telegramID")
    if name_col is None or tg_col is None:
        print(f"  WARNING: Name or telegramID column not found in {config.DRIVERS_SHEET}")
        return []

    updates = []
    letter = col_letter(name_col)

    for i, row in enumerate(values[1:], start=2):
        if tg_col >= len(row):
            continue
        tg_raw = row[tg_col].strip()
        if not tg_raw.isdigit():
            continue

        current_name = row[name_col] if name_col < len(row) else ""
        canonical = tgid_to_driver.get(int(tg_raw))

        if canonical and current_name != canonical:
            updates.append({
                "range": f"{letter}{i}",
                "values": [[canonical]],
            })
            print(f"    [{config.DRIVERS_SHEET}] row {i}: {current_name!r} -> {canonical!r}")
        elif not canonical and current_name.strip():
            print(f"    [{config.DRIVERS_SHEET}] row {i}: WARNING: no employee match for tgid={tg_raw} name={current_name!r}")

    return updates


def fix_drivers_passengers(spreadsheet, sheet_name, tgid_to_driver, norm_to_canonical):
    """Fix Name + Passenger1-4 columns. Works for both drivers_passengers and week1."""
    ws = spreadsheet.worksheet(sheet_name)
    values = ws.get_all_values()
    headers = values[0]
    col = col_map(headers)

    name_col = col.get("Name")
    tg_col = col.get("telegramID")
    pass_cols = {
        key: col.get(key)
        for key in ("Passenger1", "Passenger2", "Passenger3", "Passenger4")
        if col.get(key) is not None
    }

    if name_col is None or tg_col is None:
        print(f"  WARNING: Name or telegramID column not found in {sheet_name}")
        return []

    updates = []

    for i, row in enumerate(values[1:], start=2):
        if tg_col >= len(row):
            continue
        tg_raw = row[tg_col].strip()
        if not tg_raw.isdigit():
            continue

        # Fix driver name
        current_name = row[name_col] if name_col < len(row) else ""
        canonical = tgid_to_driver.get(int(tg_raw))
        if canonical and current_name != canonical:
            letter = col_letter(name_col)
            updates.append({
                "range": f"{letter}{i}",
                "values": [[canonical]],
            })
            print(f"    [{sheet_name}] row {i} Name: {current_name!r} -> {canonical!r}")

        # Fix passenger names
        for key, pc in pass_cols.items():
            if pc >= len(row):
                continue
            pname = row[pc]
            if not pname.strip():
                continue
            pn = normalize_text(pname)
            p_canonical = norm_to_canonical.get(pn)
            if p_canonical and pname != p_canonical:
                letter = col_letter(pc)
                updates.append({
                    "range": f"{letter}{i}",
                    "values": [[p_canonical]],
                })
                print(f"    [{sheet_name}] row {i} {key}: {pname!r} -> {p_canonical!r}")
            elif not p_canonical:
                print(f"    [{sheet_name}] row {i} {key}: WARNING: no employee match for {pname!r}")

    return updates


def fix_svodka(spreadsheet, norm_to_canonical):
    """Fix Водитель column by normalize_text matching."""
    sheet_name = "Сводка за неделю"
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"  WARNING: sheet '{sheet_name}' not found, skipping")
        return []

    values = ws.get_all_values()
    headers = values[0]
    col = col_map(headers)

    driver_col = col.get("Водитель")
    if driver_col is None:
        print(f"  WARNING: 'Водитель' column not found in {sheet_name}")
        return []

    updates = []
    letter = col_letter(driver_col)

    for i, row in enumerate(values[1:], start=2):
        if driver_col >= len(row):
            continue
        current = row[driver_col]
        if not current.strip():
            continue
        n = normalize_text(current)
        canonical = norm_to_canonical.get(n)
        if canonical and current != canonical:
            updates.append({
                "range": f"{letter}{i}",
                "values": [[canonical]],
            })
            print(f"    [{sheet_name}] row {i}: {current!r} -> {canonical!r}")
        elif not canonical:
            print(f"    [{sheet_name}] row {i}: WARNING: no employee match for {current!r}")

    return updates


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Normalize names across all sheets")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    args = parser.parse_args()

    config = Config()
    if not config.GOOGLE_CREDENTIALS or not config.SPREADSHEET_ID:
        print("ERROR: GOOGLE_CREDENTIALS and SPREADSHEET_ID env vars required")
        sys.exit(1)

    print("Connecting to Google Sheets...")
    spreadsheet = connect(config)

    print("\n1. Building canonical name maps from employees...")
    tgid_to_driver, norm_to_canonical = build_maps(spreadsheet, config)

    all_updates: dict[str, list] = {}

    print(f"\n2. Fixing '{config.EMPLOYEES_SHEET}' (TRIM)...")
    all_updates[config.EMPLOYEES_SHEET] = fix_employees(spreadsheet, config, norm_to_canonical)

    print(f"\n3. Fixing '{config.DRIVERS_SHEET}' (by telegramID)...")
    all_updates[config.DRIVERS_SHEET] = fix_drivers(spreadsheet, config, tgid_to_driver)

    print(f"\n4. Fixing '{config.DRIVERS_PASSENGERS_SHEET}' (driver by tgid, passengers by name)...")
    all_updates[config.DRIVERS_PASSENGERS_SHEET] = fix_drivers_passengers(
        spreadsheet, config.DRIVERS_PASSENGERS_SHEET, tgid_to_driver, norm_to_canonical,
    )

    print("\n5. Fixing 'week1' (driver by tgid, passengers by name)...")
    all_updates["week1"] = fix_drivers_passengers(
        spreadsheet, "week1", tgid_to_driver, norm_to_canonical,
    )

    print("\n6. Fixing 'Сводка за неделю' (by name)...")
    all_updates["Сводка за неделю"] = fix_svodka(spreadsheet, norm_to_canonical)

    # Summary
    total = sum(len(v) for v in all_updates.values())
    print(f"\n{'='*60}")
    print(f"SUMMARY: {total} changes across {sum(1 for v in all_updates.values() if v)} sheets")
    for sheet, upd in all_updates.items():
        if upd:
            print(f"  {sheet}: {len(upd)} changes")
    print(f"{'='*60}")

    if not total:
        print("\nNothing to change. All names are already normalized.")
        return

    if not args.apply:
        print("\nDRY RUN — no changes applied. Run with --apply to apply.")
        return

    # Apply
    print("\nApplying changes...")
    for sheet, upd in all_updates.items():
        if not upd:
            continue
        ws = spreadsheet.worksheet(sheet)
        ws.batch_update(upd, value_input_option="RAW")
        print(f"  OK: {sheet}: {len(upd)} cells updated")

    print("\nDone! All names normalized.")


if __name__ == "__main__":
    main()
