# Bi-Weekly Driver Report System

## Overview

System for generating fair bi-weekly driver compensation reports by cross-referencing daily bot snapshots with manager-provided timesheets. Drivers receive extra pay for days they carried 2+ verified passengers.

## Problem

- Drivers get extra pay for carrying 2+ passengers
- High employee turnover between shifts (Day, Night, Meltech Day, Meltech Night)
- Passengers may switch drivers mid-week
- Drivers may forget to update the bot
- Substitute drivers can't claim passengers already assigned to another driver
- Need fair, auditable system that minimizes both overpayment and underpayment

## Data Sources

### 1. Daily Snapshots (existing, automated)

- `appendDriversPassengersToWeek1()` runs daily at 21:00
- Captures current `drivers_passengers` state into `week1`
- Each snapshot has unique key: `SK|YYYY-MM-DD|21:00`
- 7 snapshots per week, ~40-85 drivers per snapshot
- Sunday 22:00: rotation `week1 -> week2 -> week3 -> week4`

### 2. Timesheets (manual, from managers)

- Received twice per week: Tuesdays (roster updates) and Saturdays (timesheet data)
- Two sheets per week: AMAZON (Day + Night shifts) and MELTECH (Meltech Day + Meltech Night shifts)
- Naming convention: `MMDDYYYY-MMDDYYYY AMAZON` / `MMDDYYYY-MMDDYYYY MELTECH`
- Pasted manually into the same Google Spreadsheet
- Format: Name | Monday hours | Tuesday hours | ... | Sunday hours | Total | Days worked
- Employees may appear in BOTH sheets (moved between locations mid-week)
- Header row 1: dates, Header row 2: day names, Data starts row 3
- Name column: B (index 1), Day columns: C-I (indices 2-8, Mon-Sun)

### 3. Manual Adjustments (new)

- Sheet `_manual_adjustments` in the same spreadsheet
- Admin fills in corrections for days where bot data was wrong
- Format: `Date | Driver | Passenger1 | Passenger2 | Passenger3 | Passenger4 | Reason`
- When present, adjustment OVERRIDES the snapshot for that driver+date

## Algorithm

### Credit Calculation (per driver, per day)

```
For each day in the week:
  1. Get driver's passengers from:
     a. _manual_adjustments (if entry exists for this driver+date) — takes priority
     b. Otherwise: daily snapshot for this date
  2. Check: is the driver in any timesheet (AMAZON or MELTECH) for this day?
     - Sunday: auto-yes (no timesheet verification needed)
     - Not in timesheet: day NOT credited, log anomaly DRIVER_NO_TIMESHEET
  3. For each passenger:
     - Is the passenger in any timesheet for this day?
     - Sunday: auto-verified
     - Not in timesheet: passenger not counted as verified
  4. Count verified passengers >= 2?
     - Yes: day credited (+1)
     - No: day not credited, log anomaly if passengers were claimed but unverified
```

### Timesheet Lookup

To check if person X worked on day D:
1. Find timesheet sheets matching the week's date range
2. Check BOTH AMAZON and MELTECH sheets
3. Person is present if their name appears AND the cell for day D is non-empty (has hours)

Name matching: case-insensitive, trimmed, normalized (same `normName_()` function).

### Sunday Rule

Sundays have no timesheet verification. If a driver has 2+ passengers in the snapshot on Sunday, the day is automatically credited. All passengers are auto-verified on Sundays.

## Anomaly Detection

Anomalies are detected during report generation (Saturday, after timesheets are inserted).

### Anomaly Types

| Code | Description | Trigger |
|------|-------------|---------|
| `DRIVER_NO_TIMESHEET` | Driver in snapshot but not in any timesheet | Driver has passengers in snapshot, absent from AMAZON and MELTECH |
| `PASSENGER_NO_TIMESHEET` | Passenger claimed but not in timesheet | Specific passenger not found in any timesheet for that day |
| `DRIVER_NO_SNAPSHOT` | Driver in timesheet but no snapshot | Driver appears in timesheet but has no entry in any snapshot for that day |
| `PASSENGER_SWITCHED` | Passenger changed drivers mid-week | Passenger X found with Driver A on day N, Driver B on day N+k |
| `ALL_PASSENGERS_ABSENT` | All claimed passengers missing from timesheet | Driver has passengers in snapshot, none verified by timesheet |
| `CREDIT_LOST` | Verified passenger count dropped below 2 | Driver claimed N passengers, only M<2 verified |
| `LATE_REGISTRATION` | Driver added passengers mid-week but all in timesheet from earlier | Driver's first snapshot with passengers is day N, but driver and passengers all in timesheet from day 1 |

### Anomaly Storage

Written to sheet `_anomalies` with columns:
`Date | Type | Driver | Details | Week`

## Output

### 1. Summary Sheet ("Svodka")

| Driver | 30.03 - 05.04 | 06.04 - 12.04 | Comment |
|--------|---------------|---------------|---------|
| Marufdzhon Vakhidov | 7 | 6 | - |
| Furkat Achilov | 2 | 7 | Not in timesheet 30.03 - 05.04 |
| Anatolii Dorenko | 1 | 7 | Late registration 30.03 - 05.04 |

- Column headers use actual date ranges (DD.MM - DD.MM), not "Week1/Week2"
- Date ranges are derived from snapshot dates in week2/week3
- Values: count of credited days (driver present + 2 verified passengers)
- Comment: summary of anomalies for this driver with specific date range, "-" if clean

### 2. Anomalies Sheet ("_anomalies")

All detected anomalies for manual review. Persists between report runs (appended, not overwritten).

### 3. Telegram Notifications

#### Daily (after snapshot, 21:00)

```
Snapshot 14.04.2026
Drivers: 47 | With 2+ passengers: 31 | No passengers: 8
```

No timesheet verification (timesheets may not be available yet).

#### Bi-weekly (after report generation, Saturday)

Full summary + grouped anomalies sent to admin chat.
Also available via `/report` bot command on demand.

## Implementation Components

### GAS (google_apps_script.js)

New functions:
- `generateBiWeeklyReport()` — main report generation
  - Reads week2 + week3 (two completed weeks)
  - Finds matching timesheet sheets by date range in name
  - Builds presence map from timesheets (AMAZON + MELTECH combined)
  - Applies manual adjustments from `_manual_adjustments`
  - Runs credit algorithm per driver per day
  - Detects anomalies
  - Writes "Svodka" and "_anomalies" sheets

Helper functions:
- `findTimesheetSheets_(ss, weekStartDate, weekEndDate)` — finds AMAZON/MELTECH sheets by date range
- `buildPresenceMap_(timesheetSheets)` — builds name -> day -> present map from timesheet data
- `getSnapshotsForWeek_(weekSheet)` — groups snapshot rows by date
- `getManualAdjustments_(ss, startDate, endDate)` — reads _manual_adjustments for date range
- `detectAnomalies_(snapshots, presenceMap, adjustments)` — runs anomaly detection
- `writeSvodka_(ss, results)` — writes summary sheet
- `writeAnomalies_(ss, anomalies)` — writes anomalies sheet

### Python (bot + scripts)

New files:
- `report.py` — standalone script (like weekly.py)
  - `daily_summary()` — reads latest snapshot from week1, sends brief stats to admin
  - `biweekly_report()` — reads Svodka + _anomalies, formats and sends to admin chat

Changes to existing files:
- `handlers.py` — new admin command `/report` that triggers `biweekly_report()`
- `bot.py` — register `/report` handler

### New Sheets

| Sheet | Purpose | Created by |
|-------|---------|------------|
| `_manual_adjustments` | Admin corrections | Manual (one-time setup) |
| `_anomalies` | Detected anomalies log | GAS (generateBiWeeklyReport) |
| `Svodka` | Bi-weekly summary | GAS (generateBiWeeklyReport) |

## Edge Cases

1. **Person in both AMAZON and MELTECH same day**: Counts as present (worked somewhere)
2. **Driver with 0 passengers in snapshot**: Row exists but no credit, no anomaly (normal state)
3. **No timesheet sheets found**: Report aborts with error message, does not generate partial data
4. **Duplicate snapshot keys (e.g. :01)**: Deduplicate by date, take latest
5. **Sunday**: All participants auto-verified, no timesheet check
6. **Driver not registered in bot but in timesheet**: Logged as DRIVER_NO_SNAPSHOT for awareness
7. **Manual adjustment with 0 passengers**: Overrides snapshot — driver gets 0 credit for that day (useful for removing false positives)
8. **Timesheet name parsing fails**: Skip sheet, log warning
9. **No Tuesday update from managers**: System continues with current bot data. Snapshots still run daily with whatever state `drivers_passengers` has. No special handling needed — the system is resilient to weeks where nothing changes.

## Admin Broadcast (`/broadcast`)

One-time message to all registered drivers. Two-step confirmation to prevent accidental sends.

### Flow

```
Admin: /broadcast Завтра обновление смен, проверьте пассажиров
Bot:   "Отправить 47 водителям? ✅ Да / ❌ Нет"
Admin: ✅ Да
Bot:   "✅ Отправлено: 45 | ❌ Не доставлено: 2"
```

Text is provided directly after the command. One confirmation step, then send.

If no text provided (`/broadcast` with no arguments), bot replies: "Напиши текст после команды. Пример: /broadcast Завтра обновление смен"

### Implementation

- New conversation state: `ST_BROADCAST_CONFIRM`
- Text stored in `context.user_data["broadcast_text"]`
- Uses `sheets.get_all_driver_tgids()` (same as existing `/broadcast_keyboard`)
- Admin-only (check `ADMIN_USER_IDS`)
- Message sent as plain text (no Markdown/HTML to avoid formatting issues)
- 0.1s delay between sends (Telegram rate limiting)
- Existing `/broadcast_keyboard` and weekly check remain unchanged

## Naming Convention for Timesheet Sheets

Pattern: `MMDDYYYY-MMDDYYYY LOCATION`

Examples:
- `04062026-04122026 AMAZON`
- `04062026-04122026 MELTECH`

Parsing: extract start date and end date from sheet name, match against week2/week3 date ranges.
