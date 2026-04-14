# Bi-Weekly Driver Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate fair bi-weekly compensation reports for drivers by cross-referencing daily bot snapshots with manager timesheets, with anomaly detection and Telegram notifications.

**Architecture:** GAS handles report generation (reads snapshots + timesheets from Google Sheets, writes Svodka + anomalies). Python handles Telegram notifications (reads generated report, sends to admin) and admin commands (/broadcast, /report). Two independent codebases communicating through Google Sheets as shared storage.

**Tech Stack:** Google Apps Script (report logic), Python 3.11 + python-telegram-bot 20.7 + gspread (bot + notifications)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `C:\Users\minya\OneDrive\Рабочий стол\google_apps_script.js` | Modify | Add report generation functions |
| `report.py` | Create | Standalone script for daily summary + bi-weekly Telegram notifications |
| `handlers.py` | Modify | Add /broadcast and /report admin commands |
| `bot.py` | Modify | Register new handlers |
| `config.py` | Modify | Add new button constants |

---

### Task 1: GAS — Config additions + date parsing helpers

**Files:**
- Modify: `C:\Users\minya\OneDrive\Рабочий стол\google_apps_script.js`

- [ ] **Step 1: Add report-related config values**

Add to CONFIG object (after `employeesDriverTgidHeader`):

```javascript
  // ✅ Report generation
  svodkaSheet: 'Svodka',
  anomaliesSheet: '_anomalies',
  adjustmentsSheet: '_manual_adjustments',
```

- [ ] **Step 2: Add date parsing helper**

Add after the `normName_` function:

```javascript
/************ REPORT HELPERS ************/

/**
 * Parse "MMDDYYYY" string to Date object.
 * Example: "04062026" → Date(2026, 3, 6)
 */
function parseMDY_(s) {
  const m = parseInt(s.substring(0, 2), 10) - 1;
  const d = parseInt(s.substring(2, 4), 10);
  const y = parseInt(s.substring(4, 8), 10);
  return new Date(y, m, d);
}

/**
 * Format Date as "YYYY-MM-DD" string.
 */
function fmtDate_(date, tz) {
  return Utilities.formatDate(date, tz, 'yyyy-MM-dd');
}

/**
 * Format Date as "DD.MM" for display in Svodka headers.
 */
function fmtDateShort_(date, tz) {
  return Utilities.formatDate(date, tz, 'dd.MM');
}

/**
 * Check if a "YYYY-MM-DD" string falls on a Sunday.
 */
function isSunday_(dateStr) {
  // new Date('YYYY-MM-DD') can have timezone issues; use manual parsing
  const parts = dateStr.split('-');
  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  return d.getDay() === 0;
}
```

- [ ] **Step 3: Add timesheet sheet finder**

```javascript
/**
 * Find timesheet sheets (AMAZON / MELTECH) whose date range overlaps with [weekStart, weekEnd].
 * Sheet naming convention: "MMDDYYYY-MMDDYYYY AMAZON" or "MMDDYYYY-MMDDYYYY MELTECH".
 *
 * Returns array of { sheet: Sheet, location: "AMAZON"|"MELTECH" }.
 */
function findTimesheetSheets_(ss, weekStartStr, weekEndStr) {
  const result = [];
  const sheets = ss.getSheets();

  sheets.forEach(sh => {
    const name = sh.getName();
    const match = name.match(/^(\d{8})-(\d{8})\s+(AMAZON|MELTECH)$/i);
    if (!match) return;

    const shStart = parseMDY_(match[1]);
    const shEnd = parseMDY_(match[2]);
    const wStart = new Date(weekStartStr + 'T00:00:00');
    const wEnd = new Date(weekEndStr + 'T23:59:59');

    // Check date range overlap
    if (shStart <= wEnd && shEnd >= wStart) {
      result.push({ sheet: sh, location: match[3].toUpperCase() });
    }
  });

  return result;
}
```

- [ ] **Step 4: Verify manually**

In GAS editor, run this test:
```javascript
function testFindTimesheets_() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const found = findTimesheetSheets_(ss, '2026-04-06', '2026-04-12');
  found.forEach(f => Logger.log('%s — %s', f.sheet.getName(), f.location));
}
```
Expected: finds the AMAZON and MELTECH sheets for that week.

- [ ] **Step 5: Commit GAS file**

```bash
# GAS file is on the desktop, not in the repo — no commit needed.
# Save the file and paste into GAS editor to test.
```

---

### Task 2: GAS — buildPresenceMap_ and getSnapshotsForWeek_

**Files:**
- Modify: `C:\Users\minya\OneDrive\Рабочий стол\google_apps_script.js`

- [ ] **Step 1: Add buildPresenceMap_**

This function reads timesheet sheets and builds a lookup: can we confirm person X was at work on date Y?

```javascript
/**
 * Build presence map from timesheet sheets.
 * Returns: { normalizedName: { "YYYY-MM-DD": true, ... } }
 *
 * Reads actual dates from row 1 (columns C-I contain Date objects).
 * Employee name is in column B. Hours are in columns C-I.
 * A person is present on a day if their cell for that day is non-empty.
 *
 * Checks BOTH AMAZON and MELTECH — a person in either sheet counts as present.
 */
function buildPresenceMap_(timesheetInfos, tz) {
  const map = {};

  timesheetInfos.forEach(function(info) {
    var sheet = info.sheet;
    var lastRow = sheet.getLastRow();
    var lastCol = sheet.getLastColumn();
    if (lastRow < 3 || lastCol < 3) return;

    // Row 1, columns C-I: actual dates
    var readCols = Math.min(lastCol, 9);
    var headerDates = sheet.getRange(1, 3, 1, 7).getValues()[0];
    var dateKeys = [];

    for (var i = 0; i < 7; i++) {
      var d = headerDates[i];
      if (d instanceof Date && !isNaN(d.getTime())) {
        dateKeys.push(fmtDate_(d, tz));
      } else {
        dateKeys.push(null);
      }
    }

    // Data starts at row 3 (row 1 = dates, row 2 = day names header)
    var data = sheet.getRange(3, 1, lastRow - 2, readCols).getValues();

    data.forEach(function(row) {
      var name = normName_(String(row[1] || ''));
      if (!name) return;

      if (!map[name]) map[name] = {};

      for (var di = 0; di < 7; di++) {
        if (!dateKeys[di]) continue;
        var val = row[2 + di];
        if (val !== '' && val !== null && val !== undefined) {
          map[name][dateKeys[di]] = true;
        }
      }
    });
  });

  return map;
}
```

- [ ] **Step 2: Add getSnapshotsForWeek_**

Groups snapshot rows from a week sheet by date. Deduplicates by taking the last occurrence per driver per date.

```javascript
/**
 * Parse a week sheet into snapshot data grouped by date.
 * Returns: { "YYYY-MM-DD": { normalizedDriverName: { driver: "Name", passengers: ["P1", ...] } } }
 *
 * Deduplication: if the same driver appears twice on the same date
 * (e.g. duplicate trigger runs), the last row wins.
 */
function getSnapshotsForWeek_(weekSheet) {
  var lastRow = weekSheet.getLastRow();
  var lastCol = weekSheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return {};

  var header = weekSheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var h = header.reduce(function(acc, v, i) { acc[String(v).trim().toLowerCase()] = i; return acc; }, {});

  var cName = pickHeaderIndex_(h, ['name']);
  var cSK = pickHeaderIndex_(h, ['snapshotkey']);
  var p1 = pickHeaderIndex_(h, ['passenger1', 'passenger 1']);
  var p2 = pickHeaderIndex_(h, ['passenger2', 'passenger 2']);
  var p3 = pickHeaderIndex_(h, ['passenger3', 'passenger 3']);
  var p4 = pickHeaderIndex_(h, ['passenger4', 'passenger 4']);

  if (cName == null || cSK == null) return {};

  var passengerCols = [p1, p2, p3, p4].filter(function(c) { return c != null; });
  var data = weekSheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  var byDate = {};

  data.forEach(function(row) {
    var sk = String(row[cSK] || '').trim();
    var match = sk.match(/^SK\|(\d{4}-\d{2}-\d{2})\|/);
    if (!match) return;

    var dateStr = match[1];
    var driverName = String(row[cName] || '').trim();
    if (!driverName) return;

    var passengers = [];
    passengerCols.forEach(function(ci) {
      var p = String(row[ci] || '').trim();
      if (p) passengers.push(p);
    });

    if (!byDate[dateStr]) byDate[dateStr] = {};
    byDate[dateStr][normName_(driverName)] = {
      driver: driverName,
      passengers: passengers,
    };
  });

  return byDate;
}
```

- [ ] **Step 3: Verify manually**

```javascript
function testSnapshots_() {
  var ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  var w2 = mustGetSheet_(ss, CONFIG.week2);
  var snaps = getSnapshotsForWeek_(w2);
  var dates = Object.keys(snaps).sort();
  dates.forEach(function(d) {
    var drivers = Object.keys(snaps[d]);
    Logger.log('%s: %d drivers', d, drivers.length);
  });
}
```
Expected: 7 dates, each with 39-53 drivers (matching week2 data).

---

### Task 3: GAS — Manual adjustments reader

**Files:**
- Modify: `C:\Users\minya\OneDrive\Рабочий стол\google_apps_script.js`

- [ ] **Step 1: Add getManualAdjustments_**

```javascript
/**
 * Read manual corrections from _manual_adjustments sheet.
 * Returns: { "YYYY-MM-DD|normalizedDriverName": { driver: "Name", passengers: ["P1", ...] } }
 *
 * Sheet format: Date | Driver | Passenger1 | Passenger2 | Passenger3 | Passenger4 | Reason
 * When an adjustment exists for a driver+date, it OVERRIDES the snapshot data.
 */
function getManualAdjustments_(ss, startDateStr, endDateStr) {
  var sh = ss.getSheetByName(CONFIG.adjustmentsSheet);
  if (!sh) return {};

  var lastRow = sh.getLastRow();
  if (lastRow < 2) return {};

  var tz = ss.getSpreadsheetTimeZone();
  var lastCol = sh.getLastColumn();
  var data = sh.getRange(2, 1, lastRow - 1, Math.min(lastCol, 7)).getValues();
  var result = {};

  data.forEach(function(row) {
    var dateVal = row[0];
    if (!dateVal) return;

    var dateStr;
    if (dateVal instanceof Date && !isNaN(dateVal.getTime())) {
      dateStr = fmtDate_(dateVal, tz);
    } else {
      dateStr = String(dateVal).trim();
    }

    if (dateStr < startDateStr || dateStr > endDateStr) return;

    var driver = String(row[1] || '').trim();
    if (!driver) return;

    var passengers = [];
    for (var i = 2; i <= 5; i++) {
      var p = String(row[i] || '').trim();
      if (p) passengers.push(p);
    }

    var key = dateStr + '|' + normName_(driver);
    result[key] = { driver: driver, passengers: passengers };
  });

  return result;
}
```

---

### Task 4: GAS — Credit calculation + anomaly detection

**Files:**
- Modify: `C:\Users\minya\OneDrive\Рабочий стол\google_apps_script.js`

- [ ] **Step 1: Add calculateCredits_**

This is the core algorithm. For each driver, for each day: check timesheet, count verified passengers, detect anomalies.

```javascript
/**
 * Calculate driver credits for one week and detect anomalies.
 *
 * @param {Object} snapshots - from getSnapshotsForWeek_
 * @param {Object} presenceMap - from buildPresenceMap_
 * @param {Object} adjustments - from getManualAdjustments_
 * @param {string} weekLabel - e.g. "30.03 - 05.04" for anomaly messages
 * @returns {{ credits: Object, anomalies: Array }}
 */
function calculateCredits_(snapshots, presenceMap, adjustments, weekLabel) {
  var credits = {};   // normDriverName → { name, days, details }
  var anomalies = [];
  var dates = Object.keys(snapshots).sort();
  if (!dates.length) return { credits: credits, anomalies: anomalies };

  // Track which driver each passenger was with, for PASSENGER_SWITCHED detection
  var passengerHistory = {}; // normPassenger → { driver: normDriver, driverName: string }

  dates.forEach(function(dateStr) {
    var sunday = isSunday_(dateStr);
    var dayDrivers = snapshots[dateStr];

    Object.keys(dayDrivers).forEach(function(normDriver) {
      var entry = dayDrivers[normDriver];

      // Manual adjustment overrides snapshot
      var adjKey = dateStr + '|' + normDriver;
      if (adjustments[adjKey]) {
        entry = adjustments[adjKey];
      }

      if (!credits[normDriver]) {
        credits[normDriver] = { name: entry.driver, days: 0, details: {} };
      }

      // --- Driver presence check ---
      var driverPresent = sunday || (presenceMap[normDriver] && presenceMap[normDriver][dateStr]);

      if (!driverPresent && entry.passengers.length > 0) {
        anomalies.push({
          date: dateStr, type: 'DRIVER_NO_TIMESHEET',
          driver: entry.driver,
          details: 'В боте, но не в табеле',
          week: weekLabel,
        });
        credits[normDriver].details[dateStr] = { credited: false, verified: 0, total: entry.passengers.length };
        return; // skip to next driver
      }

      // --- Passenger verification ---
      var verified = 0;
      var unverifiedNames = [];

      entry.passengers.forEach(function(p) {
        var normP = normName_(p);
        var pPresent = sunday || (presenceMap[normP] && presenceMap[normP][dateStr]);

        if (pPresent) {
          verified++;
        } else {
          unverifiedNames.push(p);
        }

        // Passenger switch detection
        if (passengerHistory[normP] && passengerHistory[normP].driver !== normDriver) {
          anomalies.push({
            date: dateStr, type: 'PASSENGER_SWITCHED',
            driver: entry.driver,
            details: p + ': был у ' + passengerHistory[normP].driverName + ', теперь у ' + entry.driver,
            week: weekLabel,
          });
        }
        passengerHistory[normP] = { driver: normDriver, driverName: entry.driver };
      });

      // --- Credit decision ---
      var credited = driverPresent && verified >= 2;
      credits[normDriver].details[dateStr] = { credited: credited, verified: verified, total: entry.passengers.length };

      if (credited) {
        credits[normDriver].days++;
      }

      // --- Anomalies for unverified passengers ---
      if (entry.passengers.length > 0 && verified === 0 && driverPresent) {
        anomalies.push({
          date: dateStr, type: 'ALL_PASSENGERS_ABSENT',
          driver: entry.driver,
          details: 'Все ' + entry.passengers.length + ' пассажиров не в табеле',
          week: weekLabel,
        });
      } else if (entry.passengers.length >= 2 && !credited && driverPresent) {
        anomalies.push({
          date: dateStr, type: 'CREDIT_LOST',
          driver: entry.driver,
          details: 'Заявлено ' + entry.passengers.length + ', верифицировано ' + verified,
          week: weekLabel,
        });
      }

      // Individual passenger anomalies (only if not already covered by ALL_PASSENGERS_ABSENT)
      if (verified > 0 && unverifiedNames.length > 0) {
        unverifiedNames.forEach(function(p) {
          anomalies.push({
            date: dateStr, type: 'PASSENGER_NO_TIMESHEET',
            driver: entry.driver,
            details: 'Пассажир ' + p + ' не в табеле',
            week: weekLabel,
          });
        });
      }
    });
  });

  // --- LATE_REGISTRATION detection ---
  var firstDate = dates[0];
  Object.keys(credits).forEach(function(normDriver) {
    var driverDates = Object.keys(credits[normDriver].details).sort();
    if (!driverDates.length || driverDates[0] === firstDate) return;

    var firstDriverDate = driverDates[0];
    var driverInTimesheetDay1 = presenceMap[normDriver] && presenceMap[normDriver][firstDate];
    if (!driverInTimesheetDay1) return;

    // Check: did the first snapshot have passengers?
    var firstSnap = snapshots[firstDriverDate] && snapshots[firstDriverDate][normDriver];
    if (!firstSnap || firstSnap.passengers.length === 0) return;

    // Were all those passengers also in timesheet from day 1?
    var allPaxDay1 = firstSnap.passengers.every(function(p) {
      var normP = normName_(p);
      return presenceMap[normP] && presenceMap[normP][firstDate];
    });

    if (allPaxDay1) {
      anomalies.push({
        date: firstDriverDate, type: 'LATE_REGISTRATION',
        driver: credits[normDriver].name,
        details: 'Добавил пассажиров с ' + firstDriverDate + ', но все в табеле с ' + firstDate,
        week: weekLabel,
      });
    }
  });

  return { credits: credits, anomalies: anomalies };
}
```

---

### Task 5: GAS — Output writers (Svodka + Anomalies)

**Files:**
- Modify: `C:\Users\minya\OneDrive\Рабочий стол\google_apps_script.js`

- [ ] **Step 1: Add writeSvodka_**

```javascript
/**
 * Write the Svodka (summary) sheet.
 * Clears existing content and writes fresh data.
 *
 * @param {Spreadsheet} ss
 * @param {Object} creditsWeekA - older week credits (from calculateCredits_)
 * @param {Object} creditsWeekB - newer week credits
 * @param {string} labelA - e.g. "30.03 - 05.04"
 * @param {string} labelB - e.g. "06.04 - 12.04"
 * @param {Array} anomaliesA - anomalies for week A
 * @param {Array} anomaliesB - anomalies for week B
 */
function writeSvodka_(ss, creditsWeekA, creditsWeekB, labelA, labelB, anomaliesA, anomaliesB) {
  var sh = ss.getSheetByName(CONFIG.svodkaSheet);
  if (!sh) sh = ss.insertSheet(CONFIG.svodkaSheet);
  sh.clearContents();

  // Build anomaly summary per driver
  var driverAnomalySummary = {};
  function addSummary(anomalies, label) {
    anomalies.forEach(function(a) {
      var key = normName_(a.driver);
      if (!driverAnomalySummary[key]) driverAnomalySummary[key] = [];
      // Avoid duplicating same type per week
      var tag = a.type + '|' + label;
      var existing = driverAnomalySummary[key].map(function(s) { return s.tag; });
      if (existing.indexOf(tag) === -1) {
        driverAnomalySummary[key].push({ tag: tag, text: a.type + ' ' + label });
      }
    });
  }
  addSummary(anomaliesA, labelA);
  addSummary(anomaliesB, labelB);

  // Collect all unique driver names across both weeks
  var allDrivers = {};
  [creditsWeekA, creditsWeekB].forEach(function(credits) {
    Object.keys(credits).forEach(function(normD) {
      if (!allDrivers[normD]) allDrivers[normD] = credits[normD].name;
    });
  });

  // Sort by name
  var sortedDrivers = Object.keys(allDrivers).sort(function(a, b) {
    return allDrivers[a].localeCompare(allDrivers[b]);
  });

  // Header
  var header = ['Водитель', labelA, labelB, 'Комментарий'];
  var rows = [header];

  sortedDrivers.forEach(function(normD) {
    var daysA = creditsWeekA[normD] ? creditsWeekA[normD].days : 0;
    var daysB = creditsWeekB[normD] ? creditsWeekB[normD].days : 0;

    // Skip drivers with 0 days in both weeks
    if (daysA === 0 && daysB === 0) return;

    var summaries = driverAnomalySummary[normD];
    var comment = summaries && summaries.length > 0
      ? summaries.map(function(s) { return s.text; }).join('; ')
      : '-';

    rows.push([allDrivers[normD], daysA, daysB, comment]);
  });

  if (rows.length > 0) {
    sh.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  }
}
```

- [ ] **Step 2: Add writeAnomalies_**

```javascript
/**
 * Append anomalies to the _anomalies sheet (does NOT clear existing data).
 * Creates the sheet if it doesn't exist.
 */
function writeAnomalies_(ss, anomalies) {
  var sh = ss.getSheetByName(CONFIG.anomaliesSheet);
  if (!sh) {
    sh = ss.insertSheet(CONFIG.anomaliesSheet);
    sh.appendRow(['Date', 'Type', 'Driver', 'Details', 'Week']);
  }

  if (!anomalies.length) return;

  // Ensure header exists
  if (sh.getLastRow() === 0) {
    sh.appendRow(['Date', 'Type', 'Driver', 'Details', 'Week']);
  }

  var rows = anomalies.map(function(a) {
    return [a.date, a.type, a.driver, a.details, a.week];
  });

  var startRow = sh.getLastRow() + 1;
  sh.getRange(startRow, 1, rows.length, 5).setValues(rows);
}
```

---

### Task 6: GAS — Main entry point generateBiWeeklyReport()

**Files:**
- Modify: `C:\Users\minya\OneDrive\Рабочий стол\google_apps_script.js`

- [ ] **Step 1: Add the main function**

```javascript
/************ MAIN 3: Bi-weekly report ************/

/**
 * Generate bi-weekly driver compensation report.
 *
 * Reads week3 (older) and week2 (newer) snapshots,
 * cross-references with timesheet sheets (AMAZON/MELTECH),
 * applies manual adjustments, calculates credits, detects anomalies,
 * and writes results to Svodka + _anomalies sheets.
 *
 * Run manually from GAS editor after pasting timesheets on Saturday,
 * or add to a trigger.
 */
function generateBiWeeklyReport() {
  var ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  var tz = ss.getSpreadsheetTimeZone();

  // --- 1. Read snapshots ---
  var w3 = mustGetSheet_(ss, CONFIG.week3);
  var w2 = mustGetSheet_(ss, CONFIG.week2);

  var snapshotsA = getSnapshotsForWeek_(w3); // older week
  var snapshotsB = getSnapshotsForWeek_(w2); // newer week

  var datesA = Object.keys(snapshotsA).sort();
  var datesB = Object.keys(snapshotsB).sort();

  if (!datesA.length && !datesB.length) {
    Logger.log('generateBiWeeklyReport: no snapshot data in week2/week3');
    return;
  }

  // --- 2. Determine date ranges ---
  var startA = datesA.length ? datesA[0] : null;
  var endA   = datesA.length ? datesA[datesA.length - 1] : null;
  var startB = datesB.length ? datesB[0] : null;
  var endB   = datesB.length ? datesB[datesB.length - 1] : null;

  var labelA = startA && endA
    ? fmtDateShort_(new Date(startA + 'T12:00:00'), tz) + ' - ' + fmtDateShort_(new Date(endA + 'T12:00:00'), tz)
    : 'N/A';
  var labelB = startB && endB
    ? fmtDateShort_(new Date(startB + 'T12:00:00'), tz) + ' - ' + fmtDateShort_(new Date(endB + 'T12:00:00'), tz)
    : 'N/A';

  Logger.log('generateBiWeeklyReport: Week A = %s (%d days), Week B = %s (%d days)',
    labelA, datesA.length, labelB, datesB.length);

  // --- 3. Find timesheets ---
  var tsA = startA ? findTimesheetSheets_(ss, startA, endA) : [];
  var tsB = startB ? findTimesheetSheets_(ss, startB, endB) : [];

  if (!tsA.length && datesA.length) {
    Logger.log('WARNING: no timesheet sheets found for week A (%s)', labelA);
  }
  if (!tsB.length && datesB.length) {
    Logger.log('WARNING: no timesheet sheets found for week B (%s)', labelB);
  }

  // --- 4. Build presence maps ---
  var presenceA = buildPresenceMap_(tsA, tz);
  var presenceB = buildPresenceMap_(tsB, tz);

  Logger.log('generateBiWeeklyReport: presence map A = %d people, B = %d people',
    Object.keys(presenceA).length, Object.keys(presenceB).length);

  // --- 5. Manual adjustments ---
  var globalStart = startA || startB;
  var globalEnd = endB || endA;
  var adjustments = getManualAdjustments_(ss, globalStart, globalEnd);

  // --- 6. Calculate credits ---
  var resultA = calculateCredits_(snapshotsA, presenceA, adjustments, labelA);
  var resultB = calculateCredits_(snapshotsB, presenceB, adjustments, labelB);

  // --- 7. Write output ---
  writeSvodka_(ss, resultA.credits, resultB.credits, labelA, labelB,
               resultA.anomalies, resultB.anomalies);

  var allAnomalies = resultA.anomalies.concat(resultB.anomalies);
  writeAnomalies_(ss, allAnomalies);

  Logger.log('generateBiWeeklyReport: done. %d drivers in svodka, %d anomalies',
    Object.keys(resultA.credits).length + Object.keys(resultB.credits).length,
    allAnomalies.length);
}
```

- [ ] **Step 2: Test end-to-end in GAS editor**

1. Paste full script into GAS editor
2. Run `generateBiWeeklyReport()`
3. Check that `Svodka` sheet is created with correct date headers and driver data
4. Check that `_anomalies` sheet has detected anomalies
5. Verify a few drivers manually against the timesheets

- [ ] **Step 3: Save GAS file on desktop**

Save `google_apps_script.js` with all changes.

---

### Task 7: Python — /broadcast command

**Files:**
- Modify: `handlers.py`
- Modify: `bot.py`

- [ ] **Step 1: Add broadcast state constant to handlers.py**

In `handlers.py`, change the state range from `range(20, 29)` to `range(20, 30)` and add `ST_BROADCAST_CONFIRM`:

```python
(
    ST_DRIVER_NAME,
    ST_DRIVER_CAR,
    ST_DRIVER_PLATES,
    ST_ADD_PASSENGERS,
    ST_STOP_CONFIRM,
    ST_ADMIN_MODE,
    ST_ADMIN_TGID,
    ST_ADMIN_SHIFT,
    ST_REMOVE_PASSENGER,
    ST_BROADCAST_CONFIRM,
) = range(20, 30)
```

- [ ] **Step 2: Add broadcast methods to BotHandlers in handlers.py**

Add after `broadcast_keyboard` method:

```python
    # ======================================================
    # Broadcast message (admin only)
    # ======================================================

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a custom message to all drivers. Usage: /broadcast <text>"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return ConversationHandler.END

        text = " ".join(context.args) if context.args else ""
        if not text.strip():
            await self._reply(
                update,
                "Напиши текст после команды.\nПример: /broadcast Завтра обновление смен",
                reply_markup=self.kb_main(uid),
            )
            return ConversationHandler.END

        driver_tg_ids = self.sheets.get_all_driver_tgids()
        context.user_data["broadcast_text"] = text
        context.user_data["broadcast_count"] = len(driver_tg_ids)

        await self._reply(
            update,
            f"Сообщение:\n\n{text}\n\nОтправить {len(driver_tg_ids)} водителям?",
            reply_markup=ReplyKeyboardMarkup(
                [[Buttons.YES, Buttons.NO]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return ST_BROADCAST_CONFIRM

    async def broadcast_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return ConversationHandler.END

        if update.message.text != Buttons.YES:
            await self._reply(
                update,
                "Рассылка отменена.",
                reply_markup=self.kb_main(uid),
            )
            return ConversationHandler.END

        text = context.user_data.pop("broadcast_text", "")
        if not text:
            await self._reply(
                update,
                "Текст сообщения не найден. Попробуй ещё раз.",
                reply_markup=self.kb_main(uid),
            )
            return ConversationHandler.END

        driver_tg_ids = self.sheets.get_all_driver_tgids()
        sent = 0
        failed = 0

        for tg_id in driver_tg_ids:
            try:
                await context.bot.send_message(chat_id=tg_id, text=text)
                sent += 1
            except Exception:
                failed += 1
            import asyncio
            await asyncio.sleep(0.1)

        result = f"✅ Отправлено: {sent} водителям."
        if failed:
            result += f"\n❌ Не доставлено: {failed}"

        await self._reply(update, result, reply_markup=self.kb_main(uid))
        await self.log_admin(context, "Broadcast", f"sent={sent} failed={failed} text={text[:100]}", update)
        return ConversationHandler.END
```

- [ ] **Step 3: Update bot.py — import and register**

Add `ST_BROADCAST_CONFIRM` to the import in `bot.py`:

```python
from handlers import (
    BotHandlers,
    ST_DRIVER_NAME,
    ST_DRIVER_CAR,
    ST_DRIVER_PLATES,
    ST_ADD_PASSENGERS,
    ST_STOP_CONFIRM,
    ST_ADMIN_MODE,
    ST_ADMIN_TGID,
    ST_ADMIN_SHIFT,
    ST_REMOVE_PASSENGER,
    ST_BROADCAST_CONFIRM,
)
```

Add `/broadcast` as an entry point in the ConversationHandler's `entry_points` list (before the CANCEL entry):

```python
            CommandHandler("broadcast", handlers.broadcast),
```

Add `ST_BROADCAST_CONFIRM` to the `states` dict:

```python
            ST_BROADCAST_CONFIRM: [
                MessageHandler(
                    filters.Regex(f"^({Buttons.YES}|{Buttons.NO})$"),
                    handlers.broadcast_confirm,
                )
            ],
```

- [ ] **Step 4: Test /broadcast**

1. Send `/broadcast` with no text → should get usage hint
2. Send `/broadcast Test message` → should see confirmation with YES/NO
3. Press NO → should cancel
4. Send `/broadcast Test message` again → YES → should send to all drivers

- [ ] **Step 5: Commit**

```bash
git add handlers.py bot.py
git commit -m "feat: add /broadcast command for admin one-time messages to all drivers"
```

---

### Task 8: Python — report.py + /report command

**Files:**
- Create: `report.py`
- Modify: `handlers.py`
- Modify: `bot.py`

- [ ] **Step 1: Create report.py**

```python
"""
Standalone scripts for report notifications via Telegram.
Run separately from the main bot (like weekly.py).

Usage:
  python report.py --mode daily     # Send daily snapshot summary
  python report.py --mode biweekly  # Send bi-weekly report + anomalies
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import telegram

from config import Config
from sheets import SheetManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def daily_summary(bot, sheets, config):
    """Read latest snapshot from week1, send brief stats to admin."""
    values = sheets._values(config.DRIVERS_PASSENGERS_SHEET)
    if not values or len(values) < 2:
        total = 0
        with_passengers = 0
        without = 0
    else:
        headers = values[0]
        col = sheets._col_map(headers)
        p_cols = [col.get(f"Passenger{i}") for i in range(1, 5)]
        p_cols = [c for c in p_cols if c is not None]

        total = 0
        with_passengers = 0
        without = 0

        for row in values[1:]:
            name_col = col.get("Name")
            if name_col is None or name_col >= len(row):
                continue
            name = (row[name_col] or "").strip()
            if not name:
                continue
            total += 1
            pax_count = sum(
                1 for c in p_cols
                if c < len(row) and (row[c] or "").strip()
            )
            if pax_count >= 2:
                with_passengers += 1
            elif pax_count == 0:
                without += 1

    from datetime import datetime
    today = datetime.now().strftime("%d.%m.%Y")

    text = (
        f"\U0001f4f8 Snapshot {today}\n"
        f"Водителей: {total} | "
        f"С 2+ пассажирами: {with_passengers} | "
        f"Без пассажиров: {without}"
    )

    if config.ADMIN_CHAT_ID:
        try:
            await bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=text)
            logger.info("Daily summary sent to admin chat")
        except Exception as e:
            logger.error("Failed to send daily summary: %s", e)


async def biweekly_report(bot, sheets, config):
    """Read Svodka + _anomalies sheets and send formatted report to admin."""
    # Read Svodka
    try:
        svodka_values = sheets._values("Svodka")
    except Exception:
        svodka_values = None

    if not svodka_values or len(svodka_values) < 2:
        if config.ADMIN_CHAT_ID:
            await bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text="\u26a0\ufe0f Отчёт не найден. Сначала запусти generateBiWeeklyReport() в GAS.",
            )
        return

    header = svodka_values[0]
    label_a = header[1] if len(header) > 1 else "Week A"
    label_b = header[2] if len(header) > 2 else "Week B"

    # Format summary
    lines = [f"\U0001f4ca Сводка за 2 недели\n{label_a} | {label_b}\n"]

    for row in svodka_values[1:]:
        name = row[0] if len(row) > 0 else ""
        days_a = row[1] if len(row) > 1 else 0
        days_b = row[2] if len(row) > 2 else 0
        comment = row[3] if len(row) > 3 else "-"
        if not name:
            continue
        flag = "" if comment == "-" else " \u26a0\ufe0f"
        lines.append(f"  {name}: {days_a} | {days_b}{flag}")

    summary_text = "\n".join(lines)

    # Read anomalies
    try:
        anom_values = sheets._values("_anomalies")
    except Exception:
        anom_values = None

    anomaly_text = ""
    if anom_values and len(anom_values) > 1:
        # Group by type
        by_type = {}
        for row in anom_values[1:]:
            atype = row[1] if len(row) > 1 else "UNKNOWN"
            driver = row[2] if len(row) > 2 else ""
            details = row[3] if len(row) > 3 else ""
            week = row[4] if len(row) > 4 else ""
            if atype not in by_type:
                by_type[atype] = []
            by_type[atype].append(f"  {driver}: {details} ({week})")

        anom_lines = ["\n\n\u26a0\ufe0f Аномалии:"]
        for atype, entries in by_type.items():
            anom_lines.append(f"\n{atype} ({len(entries)}):")
            # Limit to 10 per type to avoid message length issues
            for entry in entries[:10]:
                anom_lines.append(entry)
            if len(entries) > 10:
                anom_lines.append(f"  ... и ещё {len(entries) - 10}")

        anomaly_text = "\n".join(anom_lines)

    full_text = summary_text + anomaly_text

    if config.ADMIN_CHAT_ID:
        # Telegram message limit is 4096 chars
        for i in range(0, len(full_text), 4000):
            chunk = full_text[i:i + 4000]
            try:
                await bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=chunk)
            except Exception as e:
                logger.error("Failed to send report chunk: %s", e)
            await asyncio.sleep(0.1)

    logger.info("Bi-weekly report sent to admin chat")


async def run(mode: str):
    config = Config()
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    sheets = SheetManager(config)
    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)

    if mode == "daily":
        await daily_summary(bot, sheets, config)
    elif mode == "biweekly":
        await biweekly_report(bot, sheets, config)
    else:
        logger.error("Unknown mode: %s", mode)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["daily", "biweekly"],
        required=True,
        help="daily = snapshot summary, biweekly = full report + anomalies",
    )
    args = parser.parse_args()
    asyncio.run(run(args.mode))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add /report handler to handlers.py**

Add after the `broadcast_confirm` method:

```python
    # ======================================================
    # Report (admin only)
    # ======================================================

    async def report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send bi-weekly report summary to admin. Usage: /report"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        try:
            svodka_values = self.sheets._values("Svodka")
        except Exception:
            await self._reply(
                update,
                "Отчёт не найден. Сначала запусти generateBiWeeklyReport() в GAS.",
                reply_markup=self.kb_main(uid),
            )
            return

        if not svodka_values or len(svodka_values) < 2:
            await self._reply(
                update,
                "Отчёт пуст. Сначала запусти generateBiWeeklyReport() в GAS.",
                reply_markup=self.kb_main(uid),
            )
            return

        header = svodka_values[0]
        label_a = header[1] if len(header) > 1 else "Week A"
        label_b = header[2] if len(header) > 2 else "Week B"

        lines = [f"\U0001f4ca Сводка: {label_a} | {label_b}\n"]
        for row in svodka_values[1:]:
            name = row[0] if len(row) > 0 else ""
            days_a = row[1] if len(row) > 1 else 0
            days_b = row[2] if len(row) > 2 else 0
            comment = row[3] if len(row) > 3 else "-"
            if not name:
                continue
            flag = "" if comment == "-" else " \u26a0\ufe0f"
            lines.append(f"  {name}: {days_a} | {days_b}{flag}")

        text = "\n".join(lines)

        for i in range(0, len(text), 4000):
            await self._reply(update, text[i:i + 4000], reply_markup=self.kb_main(uid))
```

- [ ] **Step 3: Register /report in bot.py**

Add after the `broadcast_keyboard` handler registration:

```python
    app.add_handler(CommandHandler("report", handlers.report_command))
```

- [ ] **Step 4: Test**

1. Run `python report.py --mode daily` → admin should receive snapshot stats
2. Generate report in GAS first, then run `python report.py --mode biweekly` → admin receives full report
3. In bot, send `/report` → should receive the Svodka summary

- [ ] **Step 5: Commit**

```bash
git add report.py handlers.py bot.py
git commit -m "feat: add report.py notifications and /report admin command"
```

---

### Task 9: Integration verification

- [ ] **Step 1: End-to-end test flow**

1. Ensure `_manual_adjustments` sheet exists in Google Sheets (create empty sheet with header: `Date | Driver | Passenger1 | Passenger2 | Passenger3 | Passenger4 | Reason`)
2. Paste timesheet sheets into Google Sheets (if not already there)
3. Run `generateBiWeeklyReport()` in GAS editor
4. Verify Svodka sheet: correct date headers, driver counts, comments
5. Verify _anomalies sheet: reasonable anomaly entries
6. Run `python report.py --mode biweekly` → check Telegram message
7. Test `/broadcast Hello test` in bot → confirm → verify delivery
8. Test `/report` in bot → verify Svodka shown

- [ ] **Step 2: Final commit with all changes**

```bash
git add -A
git commit -m "feat: bi-weekly report system — GAS report generation + Telegram notifications + /broadcast"
git push origin main
```
