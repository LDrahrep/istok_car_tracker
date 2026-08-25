/************ CONFIG ************/
const CONFIG = {
  // ✅ Ссылка на таблицу (вставь свою)
  spreadsheetUrl: 'https://docs.google.com/spreadsheets/d/15H9rCrqNI6Ws3SsSBalUp6x_gYKPxKkqL4tqjTmv7G8/edit',
  // Листы
  sourceSheet: 'drivers_passengers',
  week1: 'week1',
  week2: 'week2',
  week3: 'week3',
  week4: 'week4',
  // Passenger1..Passenger4 = E:H (0-based индексы: E=4, H=7)
  passengerColStart: 4,
  passengerColEnd: 7,
  // Доп. колонки в week*
  noteHeader: 'Passenger note',
  noteText: '2 and more passengers',
  snapshotKeyHeader: 'SnapshotKey',
  snapshotHeader: 'SnapshotDateTime',
  // Время "контрольной точки" для ключа (чтобы один ключ в день)
  snapshotHour: '21:00',
  // Если не хочешь тащить заголовок из source, можно задать вручную. Оставь null.
  manualHeader: null,
  // ✅ employees sync (SOURCE OF TRUTH = drivers_passengers)
  employeesSheet: 'employees',
  // Заголовки колонок в employees (как у вас в таблице)
  employeesNameHeader: 'Employee',       // A
  employeesShiftHeader: 'Shift',         // C
  employeesRidesHeader: 'Rides with',    // D
  // ⚠️ ВАЖНО: по твоим скринам в employees колонка E называется telegramID
  // и мы используем её как "Driver TGID" (TGID водителя).
  employeesDriverTgidHeader: 'DriverTGID', // employees: ID водителя, с которым едет сотрудник
  // ✅ Report generation
  svodkaSheet: 'Svodka',
  anomaliesSheet: '_anomalies',
  adjustmentsSheet: '_manual_adjustments',
  // ✅ Columbus — отдельная площадка, отдельная сводка
  svodkaColumbusSheet: 'Svodka Columbus',
  anomaliesColumbusSheet: '_anomalies_columbus',
};

/************ MAIN 1: Daily append to week1 ************/
function appendDriversPassengersToWeek1() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const tz = ss.getSpreadsheetTimeZone();
  const src = mustGetSheet_(ss, CONFIG.sourceSheet);
  const w1 = mustGetSheet_(ss, CONFIG.week1);
  const srcLastRow = src.getLastRow();
  const srcLastCol = src.getLastColumn();
  if (srcLastRow < 2 || srcLastCol < 1) return;
  const snapshotKey = buildSnapshotKey_(new Date(), tz);
  const headerInfo = ensureWeekHeader_(src, w1, srcLastCol);
  const snapshotKeyCol = headerInfo.snapshotKeyCol;
  const snapshotDtCol = headerInfo.snapshotDtCol;
  enforcePlainTextColumns_(w1, snapshotKeyCol, snapshotDtCol);

  const raw = src.getRange(2, 1, srcLastRow - 1, srcLastCol).getValues();
  const rows = raw.filter(r => r.some(v => v !== '' && v !== null && v !== undefined));
  if (rows.length === 0) return;

  const snapshotStamp = Utilities.formatDate(new Date(), tz, "yyyy-MM-dd HH:mm:ss");
  const processed = [];
  rows.forEach(row => {
    const passengersCount = countNonEmpty_(row, CONFIG.passengerColStart, CONFIG.passengerColEnd);
    const note = passengersCount >= 2 ? CONFIG.noteText : '';
    processed.push([...row, note, snapshotKey, snapshotStamp]);
  });

  if (processed.length === 0) return;
  const startRow = w1.getLastRow() + 1;
  w1.getRange(startRow, 1, processed.length, processed[0].length).setValues(processed);
  enforcePlainTextColumns_(w1, snapshotKeyCol, snapshotDtCol);
}

/************ MAIN 2: Weekly rotation (Sunday) ************/
function rotateWeeksOnSunday() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const w1 = mustGetSheet_(ss, CONFIG.week1);
  const w2 = mustGetSheet_(ss, CONFIG.week2);
  const w3 = mustGetSheet_(ss, CONFIG.week3);
  const w4 = mustGetSheet_(ss, CONFIG.week4);
  copyValues_(w3, w4);
  copyValues_(w2, w3);
  copyValues_(w1, w2);
  w1.clearContents();
}

/************ OPTIONAL: One-time helper to create missing week sheets ************/
function ensureWeekSheetsExist() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  [CONFIG.week1, CONFIG.week2, CONFIG.week3, CONFIG.week4].forEach(name => {
    if (!ss.getSheetByName(name)) ss.insertSheet(name);
  });
}

/************ HELPERS ************/
function mustGetSheet_(ss, name) {
  const sh = ss.getSheetByName(name);
  if (!sh) throw new Error(`Не найден лист "${name}"`);
  return sh;
}

function copyValues_(fromSh, toSh) {
  const values = fromSh.getDataRange().getValues();
  toSh.clearContents();
  if (values.length && values[0].length) {
    toSh.getRange(1, 1, values.length, values[0].length).setValues(values);
  }
}

function countNonEmpty_(row, startIdx, endIdx) {
  let cnt = 0;
  const s = Math.max(0, startIdx);
  const e = Math.min(row.length - 1, endIdx);
  for (let i = s; i <= e; i++) {
    const v = row[i];
    if (v !== null && v !== '' && v !== undefined) cnt++;
  }
  return cnt;
}

function buildSnapshotKey_(date, tz) {
  const day = Utilities.formatDate(date, tz, "yyyy-MM-dd");
  return `SK|${day}|${CONFIG.snapshotHour}`;
}

function ensureWeekHeader_(src, weekSh, srcLastCol) {
  const srcHeader = CONFIG.manualHeader
    ? CONFIG.manualHeader
    : src.getRange(1, 1, 1, srcLastCol).getValues()[0];
  const expected = [
    ...srcHeader,
    CONFIG.noteHeader,
    CONFIG.snapshotKeyHeader,
    CONFIG.snapshotHeader,
  ];
  if (weekSh.getLastRow() === 0) {
    weekSh.appendRow(expected);
  } else {
    const currentLastCol = weekSh.getLastColumn();
    const currentHeader = weekSh.getRange(1, 1, 1, currentLastCol).getValues()[0];
    if (currentHeader.length < expected.length) {
      const toAdd = expected.slice(currentHeader.length);
      weekSh.getRange(1, currentHeader.length + 1, 1, toAdd.length).setValues([toAdd]);
    } else {
      const need = [CONFIG.noteHeader, CONFIG.snapshotKeyHeader, CONFIG.snapshotHeader];
      const missing = need.filter(h => currentHeader.indexOf(h) === -1);
      if (missing.length) {
        weekSh.getRange(1, currentHeader.length + 1, 1, missing.length).setValues([missing]);
      }
    }
  }
  const header = weekSh.getRange(1, 1, 1, weekSh.getLastColumn()).getValues()[0];
  const snapshotKeyCol = header.indexOf(CONFIG.snapshotKeyHeader) + 1;
  const snapshotDtCol = header.indexOf(CONFIG.snapshotHeader) + 1;
  if (snapshotKeyCol <= 0 || snapshotDtCol <= 0) {
    throw new Error('Не удалось создать/найти колонки SnapshotKey / SnapshotDateTime в week-листе.');
  }
  return { snapshotKeyCol, snapshotDtCol };
}

function hasSnapshotKey_(weekSh, snapshotKeyColIndex, snapshotKey) {
  const lastRow = weekSh.getLastRow();
  if (lastRow < 2) return false;
  const col = weekSh.getRange(2, snapshotKeyColIndex, lastRow - 1, 1).getDisplayValues();
  return col.some(r => String(r[0]).trim() === snapshotKey);
}

function enforcePlainTextColumns_(sheet, snapshotKeyCol, snapshotDtCol) {
  sheet.getRange(1, snapshotKeyCol, Math.max(1, sheet.getMaxRows()), 1).setNumberFormat('@');
  sheet.getRange(1, snapshotDtCol, Math.max(1, sheet.getMaxRows()), 1).setNumberFormat('@');
}

/************ EMPLOYEES SYNC (SOURCE OF TRUTH = drivers_passengers) ************/
function normName_(s) {
  return String(s || '').trim().toLowerCase();
}

/**
 * Нормализация смены (паритет с Python normalize_shift).
 * Day == Meltech → "day", Night → "night", пусто → "unknown", остальное → "other"
 */
function normalizeShift_(raw) {
  const s = String(raw || '')
    .replace(/\u00a0/g, ' ')
    .replace(/\u200b/g, '')
    .replace(/\ufeff/g, '')
    .trim()
    .toLowerCase();
  if (s === 'day') return 'day';
  if (s === 'night') return 'night';
  if (s === 'meltech day') return 'meltech_day';
  if (s === 'meltech night') return 'meltech_night';
  // Обратная совместимость: старое "Meltech" → meltech_day
  if (s === 'meltech') return 'meltech_day';
  if (!s) return 'unknown';
  return 'other';
}

function pickHeaderIndex_(h, variants) {
  for (const v of variants) {
    const key = String(v).trim().toLowerCase();
    if (h[key] != null) return h[key];
  }
  return null;
}

function buildPassengerToDriverMap_() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const dp = mustGetSheet_(ss, CONFIG.sourceSheet);
  const lastRow = dp.getLastRow();
  const lastCol = dp.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return new Map();
  const header = dp.getRange(1, 1, 1, lastCol).getValues()[0];
  const h = header.reduce((acc, v, i) => (acc[String(v).trim().toLowerCase()] = i, acc), {});
  const cDriverName = pickHeaderIndex_(h, ['name']);
  const cTgid = pickHeaderIndex_(h, ['tgid', 'telegramid', 'telegram id', 'telegramID']);
  const p1 = pickHeaderIndex_(h, ['passenger1', 'passenger 1']);
  const p2 = pickHeaderIndex_(h, ['passenger2', 'passenger 2']);
  const p3 = pickHeaderIndex_(h, ['passenger3', 'passenger 3']);
  const p4 = pickHeaderIndex_(h, ['passenger4', 'passenger 4']);
  if (cDriverName == null || cTgid == null) return new Map();
  const values = dp.getRange(2, 1, lastRow - 1, lastCol).getValues();
  const m = new Map();
  values.forEach(row => {
    const driverName = String(row[cDriverName] ?? '').trim();
    const driverTgid = String(row[cTgid] ?? '').trim();
    if (!driverName || !driverTgid) return;
    m.set(normName_(driverName), { driverName, driverTgid });
    [p1, p2, p3, p4].forEach(ci => {
      if (ci == null) return;
      const passenger = String(row[ci] ?? '').trim();
      if (!passenger) return;
      m.set(normName_(passenger), { driverName, driverTgid });
    });
  });
  return m;
}

function getEmployeesHeaderIndex_(empSheet) {
  const lastCol = empSheet.getLastColumn();
  const header = empSheet.getRange(1, 1, 1, lastCol).getValues()[0];
  const h = header.reduce((acc, v, i) => (acc[String(v).trim().toLowerCase()] = i, acc), {});
  const cEmpName = pickHeaderIndex_(h, [CONFIG.employeesNameHeader, 'employee', 'name', 'full name', 'сотрудник']);
  const cShift   = pickHeaderIndex_(h, [CONFIG.employeesShiftHeader, 'shift', 'смена']);
  const cRides   = pickHeaderIndex_(h, [CONFIG.employeesRidesHeader, 'rides with', 'rides_with', 'rideswith']);
  const cTgid    = pickHeaderIndex_(h, [
    CONFIG.employeesDriverTgidHeader,
    'DriverTGID',
    "driver's tgid", "driver's tgid",
    'driver tgid',
    'telegramid', 'telegram id', 'telegramID'
  ]);
  if (cEmpName == null || cShift == null || cRides == null || cTgid == null) {
    throw new Error("employees: не найдены нужные заголовки (Employee / Shift / Rides with / Driver TGID).");
  }
  return { cEmpName, cShift, cRides, cTgid, lastCol };
}

function chunkContiguous_(sortedRows) {
  const chunks = [];
  let s = sortedRows[0], prev = sortedRows[0];
  for (let i = 1; i < sortedRows.length; i++) {
    const r = sortedRows[i];
    if (r === prev + 1) {
      prev = r;
    } else {
      chunks.push([s, prev]);
      s = r; prev = r;
    }
  }
  chunks.push([s, prev]);
  return chunks;
}

function syncEmployeesRows_(rowNumbers) {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  const lastRow = emp.getLastRow();
  if (lastRow < 2) return;
  const { cEmpName, cRides, cTgid } = getEmployeesHeaderIndex_(emp);
  const map = buildPassengerToDriverMap_();
  const rows = rowNumbers
    .filter(r => r >= 2 && r <= lastRow)
    .sort((a, b) => a - b);
  if (!rows.length) return;
  const chunks = chunkContiguous_(rows);
  chunks.forEach(([start, end]) => {
    const num = end - start + 1;
    const names = emp.getRange(start, cEmpName + 1, num, 1).getValues();
    const outRides = [];
    const outTgids = [];
    for (let i = 0; i < names.length; i++) {
      const empName = String(names[i][0] ?? '').trim();
      const key = normName_(empName);
      const found = map.get(key);
      if (found) {
        outRides.push([found.driverName]);
        outTgids.push([found.driverTgid]);
      } else {
        outRides.push(['']);
        outTgids.push(['']);
      }
    }
    emp.getRange(start, cRides + 1, num, 1).setValues(outRides);
    emp.getRange(start, cTgid + 1, num, 1).setValues(outTgids);
  });
}

function syncEmployeesAll() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  const lastRow = emp.getLastRow();
  if (lastRow < 2) return;
  refreshShiftSnapshotAndCleanup_();
  const rows = [];
  for (let r = 2; r <= lastRow; r++) rows.push(r);
  syncEmployeesRows_(rows);
  // Plain-text Shift sync: пишет employees.Shift → drivers.Shift и drivers_passengers.Shift
  // без формулы (ARRAYFORMULA ломалась при удалении строк).
  syncShiftsToDpAndDrivers_();
}

/************ UNASSIGN + SHIFT CHANGE HANDLING ************/
function removePassengerOnlyFromDriversPassengers_(employeeName) {
  const target = normName_(employeeName);
  if (!target) return;
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const dp = mustGetSheet_(ss, CONFIG.sourceSheet);
  const lastRow = dp.getLastRow();
  const lastCol = dp.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return;
  const header = dp.getRange(1, 1, 1, lastCol).getValues()[0];
  const h = header.reduce((acc, v, i) => (acc[String(v).trim().toLowerCase()] = i, acc), {});
  const p1 = pickHeaderIndex_(h, ['passenger1', 'passenger 1']);
  const p2 = pickHeaderIndex_(h, ['passenger2', 'passenger 2']);
  const p3 = pickHeaderIndex_(h, ['passenger3', 'passenger 3']);
  const p4 = pickHeaderIndex_(h, ['passenger4', 'passenger 4']);
  const passengerCols = [p1, p2, p3, p4].filter(c => c != null);
  if (!passengerCols.length) return;
  const values = dp.getRange(2, 1, lastRow - 1, lastCol).getValues();
  for (let i = 0; i < values.length; i++) {
    const rowIdx = i + 2;
    const row = values[i];
    passengerCols.forEach(ci => {
      const val = normName_(row[ci]);
      if (val && val === target) {
        dp.getRange(rowIdx, ci + 1).clearContent();
      }
    });
  }
}

function removeEmployeeFromDriversPassengers_(employeeName) {
  const target = normName_(employeeName);
  if (!target) return;
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const dp = mustGetSheet_(ss, CONFIG.sourceSheet);
  const lastRow = dp.getLastRow();
  const lastCol = dp.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return;
  const header = dp.getRange(1, 1, 1, lastCol).getValues()[0];
  const h = header.reduce((acc, v, i) => (acc[String(v).trim().toLowerCase()] = i, acc), {});
  const cName = pickHeaderIndex_(h, ['name']);
  const p1 = pickHeaderIndex_(h, ['passenger1', 'passenger 1']);
  const p2 = pickHeaderIndex_(h, ['passenger2', 'passenger 2']);
  const p3 = pickHeaderIndex_(h, ['passenger3', 'passenger 3']);
  const p4 = pickHeaderIndex_(h, ['passenger4', 'passenger 4']);
  const passengerCols = [p1, p2, p3, p4].filter(c => c != null);
  const values = dp.getRange(2, 1, lastRow - 1, lastCol).getValues();
  for (let i = 0; i < values.length; i++) {
    const rowIdx = i + 2;
    const row = values[i];
    if (cName != null) {
      const driverName = normName_(row[cName]);
      if (driverName && driverName === target) {
        dp.getRange(rowIdx, 1, 1, lastCol).clearContent();
        continue;
      }
    }
    passengerCols.forEach(ci => {
      const val = normName_(row[ci]);
      if (val && val === target) {
        dp.getRange(rowIdx, ci + 1).clearContent();
      }
    });
  }
}

function clearEmployeesDE_(row) {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  const { cRides, cTgid } = getEmployeesHeaderIndex_(emp);
  emp.getRange(row, cRides + 1).clearContent();
  emp.getRange(row, cTgid + 1).clearContent();
}

/**
 * Очистить Rides with + telegramID у сотрудников по списку имён.
 */
function clearEmployeesDEByNames_(names) {
  if (!names || !names.length) return;
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  const lastRow = emp.getLastRow();
  if (lastRow < 2) return;
  const { cEmpName, cRides, cTgid } = getEmployeesHeaderIndex_(emp);
  const targetSet = new Set(names.map(n => normName_(n)));
  const empNames = emp.getRange(2, cEmpName + 1, lastRow - 1, 1).getValues();
  for (let i = 0; i < empNames.length; i++) {
    const name = normName_(empNames[i][0]);
    if (targetSet.has(name)) {
      const row = i + 2;
      emp.getRange(row, cRides + 1).clearContent();
      emp.getRange(row, cTgid + 1).clearContent();
    }
  }
}

/**
 * Обработка смены Shift у сотрудника.
 *
 * ВАЖНО: с версии 14.04.2026 функция НЕ действует сразу, а ставит задачу в очередь.
 * Задачи обрабатываются функцией processPendingShiftChanges_() спустя 10 минут.
 * Это даёт админу время обновить смены всех связанных сотрудников сразу,
 * без промежуточных удалений пассажиров из drivers_passengers.
 */
function handleShiftChangeRow_(row, oldShift, newShift) {
  // ОТКЛЮЧЕНО: авто-очистка пассажиров при смене Shift. Сотрудники часто
  // меняют смены, и очистка удаляла ещё актуальных пассажиров раньше, чем
  // админ успевал обновить данные. Чтобы вернуть — убери этот return.
  return;

  if (row < 2) return;
  if (String(oldShift ?? '').trim() === String(newShift ?? '').trim()) return;

  const oldNorm = normalizeShift_(oldShift);
  const newNorm = normalizeShift_(newShift);
  if (oldNorm === newNorm) return; // одинаковая нормализованная смена — ничего не делаем

  queueShiftChange_(row, oldShift, newShift);
}

/************ SHIFT CHANGE QUEUE (10-minute delayed processing) ************/

var SHIFT_CHANGE_TIMEOUT_MS = 10 * 60 * 1000; // 10 минут

/**
 * Добавить задачу на обработку смены в очередь.
 * Обрабатывается processPendingShiftChanges_() спустя 10 минут.
 */
function queueShiftChange_(row, oldShift, newShift) {
  var props = PropertiesService.getScriptProperties();
  var rawQueue = props.getProperty('PENDING_SHIFT_CHANGES');
  var queue = rawQueue ? JSON.parse(rawQueue) : [];

  // Дедуп: если для этой строки уже есть задача, обновляем oldShift (первое значение) и newShift
  var existing = null;
  for (var i = 0; i < queue.length; i++) {
    if (queue[i].row === row) {
      existing = queue[i];
      break;
    }
  }
  if (existing) {
    // oldShift оставляем от первой задачи (что было до изменений), newShift обновляем
    existing.newShift = String(newShift || '');
    existing.queuedAt = Date.now(); // перезапускаем таймер
  } else {
    queue.push({
      row: row,
      oldShift: String(oldShift || ''),
      newShift: String(newShift || ''),
      queuedAt: Date.now()
    });
  }

  props.setProperty('PENDING_SHIFT_CHANGES', JSON.stringify(queue));
  Logger.log('queueShiftChange_: queued row=%s old=%s new=%s (queue size=%d)',
    row, oldShift, newShift, queue.length);
}

/**
 * Обработать отложенные задачи. Вызывается триггером каждые 2 минуты.
 * Выполняет только те задачи, что были поставлены более 10 минут назад.
 */
function processPendingShiftChanges_() {
  var props = PropertiesService.getScriptProperties();
  var rawQueue = props.getProperty('PENDING_SHIFT_CHANGES');
  if (!rawQueue) return;

  var queue = JSON.parse(rawQueue);
  var now = Date.now();
  var remaining = [];

  queue.forEach(function(entry) {
    if (now - entry.queuedAt < SHIFT_CHANGE_TIMEOUT_MS) {
      remaining.push(entry);
      return;
    }

    try {
      executeDelayedShiftChange_(entry);
    } catch (e) {
      Logger.log('processPendingShiftChanges_: error row=%s: %s', entry.row, e.message);
      notifyAdmin_('⚠️ Ошибка обработки смены для row=' + entry.row + ': ' + e.message);
    }
  });

  props.setProperty('PENDING_SHIFT_CHANGES', JSON.stringify(remaining));
}

/**
 * Выполнить отложенную задачу со свежей проверкой состояния и сравнением со snapshot'ом.
 */
function executeDelayedShiftChange_(entry) {
  var ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  var emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  var headerInfo = getEmployeesHeaderIndex_(emp);
  var cEmpName = headerInfo.cEmpName;
  var cShift = headerInfo.cShift;

  var empName = String(emp.getRange(entry.row, cEmpName + 1).getValue()).trim();
  if (!empName) {
    Logger.log('executeDelayedShiftChange_: row=%s has no name, skipping', entry.row);
    return;
  }

  var currentShift = String(emp.getRange(entry.row, cShift + 1).getValue()).trim();
  var oldNorm = normalizeShift_(entry.oldShift);
  var currentNorm = normalizeShift_(currentShift);

  // Если смена вернулась к исходной — ничего не делаем
  if (oldNorm === currentNorm) {
    Logger.log('executeDelayedShiftChange_: %s shift reverted to %s, skipping',
      empName, currentShift);
    return;
  }

  // Собираем snapshot за вчерашний день для сравнения
  var yesterdaySnapshot = getYesterdaySnapshot_(ss, empName);

  // Выполняем оригинальную логику (CASE 1 / CASE 2)
  var actionLog = [];
  performShiftChangeWithLogging_(entry.row, entry.oldShift, currentShift, actionLog);

  // Сравниваем с yesterday's snapshot
  var snapshotInfo = '';
  if (yesterdaySnapshot) {
    if (yesterdaySnapshot.role === 'driver') {
      snapshotInfo = '\nВчера (' + yesterdaySnapshot.date + ') у него были пассажиры: ' +
        (yesterdaySnapshot.passengers.join(', ') || '(нет)');
    } else if (yesterdaySnapshot.role === 'passenger') {
      snapshotInfo = '\nВчера (' + yesterdaySnapshot.date + ') он был пассажиром у: ' +
        yesterdaySnapshot.driverName;
    }
  }

  if (actionLog.length > 0) {
    var msg = '⚠️ Обработана смена (спустя 10 мин):\n' +
      'Сотрудник: ' + empName + '\n' +
      'Старая смена: ' + entry.oldShift + '\n' +
      'Новая смена: ' + currentShift + '\n' +
      'Действия:\n  • ' + actionLog.join('\n  • ') +
      snapshotInfo;
    notifyAdmin_(msg);
    Logger.log(msg);
  } else {
    Logger.log('executeDelayedShiftChange_: %s no actions needed', empName);
  }
}

/**
 * Найти в последнем snapshot'е (из week1) информацию о сотруднике.
 * Возвращает: { role: 'driver'|'passenger'|null, date, passengers?, driverName? }
 */
function getYesterdaySnapshot_(ss, empName) {
  var w1 = ss.getSheetByName(CONFIG.week1);
  if (!w1) return null;

  var lastRow = w1.getLastRow();
  var lastCol = w1.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return null;

  var header = w1.getRange(1, 1, 1, lastCol).getValues()[0];
  var h = header.reduce(function(acc, v, i) { acc[String(v).trim().toLowerCase()] = i; return acc; }, {});
  var cName = pickHeaderIndex_(h, ['name']);
  var cSK = pickHeaderIndex_(h, ['snapshotkey']);
  var p1 = pickHeaderIndex_(h, ['passenger1', 'passenger 1']);
  var p2 = pickHeaderIndex_(h, ['passenger2', 'passenger 2']);
  var p3 = pickHeaderIndex_(h, ['passenger3', 'passenger 3']);
  var p4 = pickHeaderIndex_(h, ['passenger4', 'passenger 4']);
  var passengerCols = [p1, p2, p3, p4].filter(function(c) { return c != null; });

  if (cName == null || cSK == null) return null;

  var target = normName_(empName);
  var data = w1.getRange(2, 1, lastRow - 1, lastCol).getValues();

  // Идём с конца — берём самый свежий snapshot
  var latestByDriver = null;
  var latestByPassenger = null;

  for (var i = data.length - 1; i >= 0; i--) {
    var row = data[i];
    var sk = String(row[cSK] || '').trim();
    var match = sk.match(/^SK\|(\d{4}-\d{2}-\d{2})\|/);
    if (!match) continue;
    var dateStr = match[1];

    var driverName = normName_(row[cName]);
    if (driverName === target && !latestByDriver) {
      var pax = [];
      passengerCols.forEach(function(ci) {
        var p = String(row[ci] || '').trim();
        if (p) pax.push(p);
      });
      latestByDriver = { role: 'driver', date: dateStr, passengers: pax };
      break; // нашли самую свежую запись как водитель — хватит
    }

    if (!latestByPassenger) {
      for (var ci of passengerCols) {
        if (normName_(row[ci]) === target) {
          latestByPassenger = {
            role: 'passenger',
            date: dateStr,
            driverName: String(row[cName] || '').trim()
          };
          break;
        }
      }
    }
  }

  return latestByDriver || latestByPassenger;
}

/**
 * Выполнить логику смены с логированием всех действий.
 * Делегирует performShiftChange_, но перехватывает clearContent вызовы.
 * Для простоты используем повторную реализацию с логом.
 */
function performShiftChangeWithLogging_(row, oldShift, newShift, actionLog) {
  // ОТКЛЮЧЕНО: см. handleShiftChangeRow_. Ничего не удаляем по смене Shift
  // (страховка на случай уже стоящих в очереди задач). Чтобы вернуть — убери return.
  return;

  if (row < 2) return;
  if (String(oldShift ?? '').trim() === String(newShift ?? '').trim()) return;

  var oldNorm = normalizeShift_(oldShift);
  var newNorm = normalizeShift_(newShift);
  if (oldNorm === newNorm) return;

  var ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  var emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  var empLastRow = emp.getLastRow();
  var headerInfo = getEmployeesHeaderIndex_(emp);
  var cEmpName = headerInfo.cEmpName;
  var cShift = headerInfo.cShift;
  var empName = String(emp.getRange(row, cEmpName + 1).getValue()).trim();
  if (!empName) return;

  // Карта имя → смена из employees (свежие значения)
  var empShiftMap = {};
  if (empLastRow >= 2) {
    var names = emp.getRange(2, cEmpName + 1, empLastRow - 1, 1).getValues();
    var shifts = emp.getRange(2, cShift + 1, empLastRow - 1, 1).getValues();
    for (var i = 0; i < names.length; i++) {
      var n = String(names[i][0] ?? '').trim();
      if (n) empShiftMap[normName_(n)] = String(shifts[i][0] ?? '').trim();
    }
  }

  var dp = mustGetSheet_(ss, CONFIG.sourceSheet);
  var dpLastRow = dp.getLastRow();
  var dpLastCol = dp.getLastColumn();
  if (dpLastRow < 2 || dpLastCol < 1) return;

  var header = dp.getRange(1, 1, 1, dpLastCol).getValues()[0];
  var h = header.reduce(function(acc, v, i) { acc[String(v).trim().toLowerCase()] = i; return acc; }, {});
  var cName = pickHeaderIndex_(h, ['name']);
  var p1 = pickHeaderIndex_(h, ['passenger1', 'passenger 1']);
  var p2 = pickHeaderIndex_(h, ['passenger2', 'passenger 2']);
  var p3 = pickHeaderIndex_(h, ['passenger3', 'passenger 3']);
  var p4 = pickHeaderIndex_(h, ['passenger4', 'passenger 4']);
  var passengerCols = [p1, p2, p3, p4].filter(function(c) { return c != null; });

  var dpValues = dp.getRange(2, 1, dpLastRow - 1, dpLastCol).getValues();
  var target = normName_(empName);

  // CASE 1: сотрудник — водитель
  if (cName != null) {
    for (var i = 0; i < dpValues.length; i++) {
      var driverName = normName_(dpValues[i][cName]);
      if (driverName !== target) continue;

      var dpRowIdx = i + 2;
      var removedNames = [];

      for (var ci of passengerCols) {
        var pName = String(dpValues[i][ci] ?? '').trim();
        if (!pName) continue;
        var pShiftRaw = empShiftMap[normName_(pName)] || '';
        var pShiftNorm = normalizeShift_(pShiftRaw);
        if (pShiftNorm !== newNorm) {
          dp.getRange(dpRowIdx, ci + 1).clearContent();
          removedNames.push(pName + ' (смена: ' + pShiftRaw + ')');
        }
      }

      if (removedNames.length) {
        clearEmployeesDEByNames_(removedNames.map(function(n) { return n.split(' (смена:')[0]; }));
        actionLog.push('Удалены из ' + empName + ': ' + removedNames.join(', '));
      }
      return;
    }
  }

  // CASE 2: сотрудник — пассажир
  for (var i = 0; i < dpValues.length; i++) {
    var dpRow = dpValues[i];
    for (var ci of passengerCols) {
      var pName = normName_(dpRow[ci]);
      if (pName !== target) continue;

      if (cName == null) continue;
      var driverName = String(dpRow[cName] ?? '').trim();
      var driverShiftRaw = empShiftMap[normName_(driverName)] || '';
      var driverShiftNorm = normalizeShift_(driverShiftRaw);

      if (newNorm !== driverShiftNorm) {
        var dpRowIdx = i + 2;
        dp.getRange(dpRowIdx, ci + 1).clearContent();
        clearEmployeesDEByNames_([empName]);
        actionLog.push(empName + ' удалён как пассажир у ' + driverName +
          ' (смена водителя: ' + driverShiftRaw + ')');
      }
      return;
    }
  }
}

/**
 * Отправить сообщение админу в Telegram.
 * Требует Script Properties: TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID.
 * Если не настроены — просто пишет в Logger.
 */
function notifyAdmin_(text) {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('TELEGRAM_BOT_TOKEN');
  var chatId = props.getProperty('ADMIN_CHAT_ID');

  if (!token || !chatId) {
    Logger.log('notifyAdmin_ (no Telegram creds): %s', text);
    return;
  }

  try {
    var url = 'https://api.telegram.org/bot' + token + '/sendMessage';
    UrlFetchApp.fetch(url, {
      method: 'post',
      payload: {
        chat_id: chatId,
        text: text.substring(0, 4000)
      },
      muteHttpExceptions: true
    });
  } catch (e) {
    Logger.log('notifyAdmin_: failed to send Telegram message: %s', e.message);
  }
}

/**
 * Создать триггер для обработки очереди каждые 2 минуты.
 */
function createShiftChangeQueueTrigger() {
  var functionName = 'processPendingShiftChanges_';
  var existing = ScriptApp.getProjectTriggers();
  var alreadyExists = existing.some(function(t) { return t.getHandlerFunction() === functionName; });
  if (alreadyExists) {
    Logger.log('Shift change queue trigger already exists');
    return;
  }
  ScriptApp.newTrigger(functionName)
    .timeBased()
    .everyMinutes(5)
    .create();
  Logger.log('Shift change queue trigger created: каждые 5 минут');
}

/************ SHIFT SNAPSHOT (for formula updates) ************/
function loadShiftSnapshot_() {
  const props = PropertiesService.getScriptProperties();
  const raw = props.getProperty('EMP_SHIFT_SNAPSHOT');
  if (!raw) return {};
  try { return JSON.parse(raw); } catch (e) { return {}; }
}

function saveShiftSnapshot_(snap) {
  PropertiesService.getScriptProperties().setProperty('EMP_SHIFT_SNAPSHOT', JSON.stringify(snap || {}));
}

function refreshShiftSnapshotAndCleanup_() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  const lastRow = emp.getLastRow();
  if (lastRow < 2) return;
  const { cEmpName, cShift } = getEmployeesHeaderIndex_(emp);
  const prev = loadShiftSnapshot_();
  const next = {};
  const names = emp.getRange(2, cEmpName + 1, lastRow - 1, 1).getValues();
  const shifts = emp.getRange(2, cShift + 1, lastRow - 1, 1).getValues();
  for (let i = 0; i < names.length; i++) {
    const row = i + 2;
    const name = String(names[i][0] ?? '').trim();
    const shift = String(shifts[i][0] ?? '').trim();
    if (!name) continue;
    const key = normName_(name);
    next[key] = shift;
    const oldShift = prev[key];
    if (oldShift !== undefined && String(oldShift).trim() !== shift) {
      handleShiftChangeRow_(row, oldShift, shift);
    }
  }
  saveShiftSnapshot_(next);
}

/************ onEdit (installable trigger) ************/
function onEmployeesEdit(e) {
  try {
    const range = e.range;
    const sheet = range.getSheet();
    if (sheet.getName() !== CONFIG.employeesSheet) return;
    const row = range.getRow();
    if (row < 2) return;
    const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
    const emp = mustGetSheet_(ss, CONFIG.employeesSheet);
    const { cEmpName, cShift, cRides, cTgid } = getEmployeesHeaderIndex_(emp);
    if (range.getColumn() === (cShift + 1)) {
      handleShiftChangeRow_(row, e.oldValue, range.getValue());
      return;
    }
    if (range.getColumn() === (cRides + 1) || range.getColumn() === (cTgid + 1)) {
      const ridesNow = String(emp.getRange(row, cRides + 1).getValue() ?? '').trim();
      const tgidNow = String(emp.getRange(row, cTgid + 1).getValue() ?? '').trim();
      if (!ridesNow && !tgidNow) {
        const empName = String(emp.getRange(row, cEmpName + 1).getValue() ?? '').trim();
        if (empName) removePassengerOnlyFromDriversPassengers_(empName);
      }
      return;
    }
    syncEmployeesRows_([row]);
  } catch (err) {
    Logger.log(err);
  }
}

/************ TRIGGERS ************/
function createWeeklyRotationTrigger() {
  const functionName = 'rotateWeeksOnSunday';
  const existing = ScriptApp.getProjectTriggers();
  const alreadyExists = existing.some(t => t.getHandlerFunction() === functionName);
  if (alreadyExists) {
    Logger.log('Weekly trigger уже существует.');
    return;
  }
  ScriptApp.newTrigger(functionName)
    .timeBased()
    .onWeekDay(ScriptApp.WeekDay.SUNDAY)
    .atHour(22)
    .create();
  Logger.log('Weekly trigger создан: воскресенье 22:00');
}

function createDailyAppendTrigger() {
  const functionName = 'appendDriversPassengersToWeek1';
  const existing = ScriptApp.getProjectTriggers();
  const alreadyExists = existing.some(t => t.getHandlerFunction() === functionName);
  if (alreadyExists) {
    Logger.log('Daily append trigger уже существует.');
    return;
  }
  ScriptApp.newTrigger(functionName)
    .timeBased()
    .everyDays(1)
    .atHour(21)
    .create();
  Logger.log('Daily append trigger создан: каждый день 21:00');
}

function createEmployeesEditTrigger() {
  const functionName = 'onEmployeesEdit';
  const existing = ScriptApp.getProjectTriggers();
  const alreadyExists = existing.some(t => t.getHandlerFunction() === functionName && t.getEventType() === ScriptApp.EventType.ON_EDIT);
  if (alreadyExists) {
    Logger.log('Employees onEdit trigger уже существует.');
    return;
  }
  ScriptApp.newTrigger(functionName)
    .forSpreadsheet(SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl))
    .onEdit()
    .create();
  Logger.log('Employees onEdit trigger создан.');
}

function createEmployeesTimeSyncTrigger() {
  const functionName = 'syncEmployeesAll';
  const existing = ScriptApp.getProjectTriggers();
  const alreadyExists = existing.some(t => t.getHandlerFunction() === functionName && t.getEventType() === ScriptApp.EventType.CLOCK);
  if (alreadyExists) {
    Logger.log('Employees time trigger уже существует.');
    return;
  }
  ScriptApp.newTrigger(functionName)
    .timeBased()
    .everyMinutes(5)
    .create();
  Logger.log('Employees time trigger создан: каждые 5 минут.');
}

/************ SHIFT SYNC (plain text, no formula) ************/
/*
 * История: раньше колонка `Shift` в `drivers` и `drivers_passengers` была
 * заполнена через ARRAYFORMULA с открытым диапазоном A2:A. Это ломалось
 * каждый раз при удалении строк внутри диапазона — Sheets автоматически
 * укорачивал формулу до A2:A<N>. На это пытались поставить часовой
 * formula-guard, но он только лечил симптом.
 *
 * Сейчас: Shift пишется как plain text каждые 5 минут из `syncEmployeesAll`.
 * Никаких формул в D — удаление строк безопасно, восстанавливать нечего.
 */

function colToLetter_(col) {
  let letter = '';
  while (col > 0) {
    const mod = (col - 1) % 26;
    letter = String.fromCharCode(65 + mod) + letter;
    col = Math.floor((col - mod - 1) / 26);
  }
  return letter;
}

function findHeaderCol_(sheet, headerText) {
  const lastCol = sheet.getLastColumn();
  if (lastCol < 1) return null;
  const headers = sheet.getRange(1, 1, 1, lastCol).getDisplayValues()[0];
  const target = String(headerText).trim().toLowerCase();
  const idx = headers.findIndex(h => String(h).trim().toLowerCase() === target);
  return idx === -1 ? null : (idx + 1);
}

/**
 * Синхронизирует колонку Shift в листах drivers и drivers_passengers
 * с employees.Shift — пишет plain-text значения через batch setValues.
 *
 * Вызывается из syncEmployeesAll каждые 5 минут.
 * Может быть запущена вручную через Run → fixFormulas (alias) или Run → syncShiftsManual.
 */
function syncShiftsToDpAndDrivers_() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  const emp = mustGetSheet_(ss, CONFIG.employeesSheet);
  const empLastRow = emp.getLastRow();
  if (empLastRow < 2) return;

  // Map: norm(name) → shift
  const { cEmpName, cShift } = getEmployeesHeaderIndex_(emp);
  const names  = emp.getRange(2, cEmpName + 1, empLastRow - 1, 1).getValues();
  const shifts = emp.getRange(2, cShift   + 1, empLastRow - 1, 1).getValues();
  const shiftMap = {};
  for (let i = 0; i < names.length; i++) {
    const n = String(names[i][0] || '').trim();
    if (!n) continue;
    shiftMap[normName_(n)] = String(shifts[i][0] || '').trim();
  }

  let dpStats = syncShiftColumn_(ss, CONFIG.sourceSheet, shiftMap);
  let drvStats = syncShiftColumn_(ss, 'drivers', shiftMap);
  Logger.log('syncShiftsToDpAndDrivers_: dp updated=%d/%d, drivers updated=%d/%d',
             dpStats.updated, dpStats.total, drvStats.updated, drvStats.total);
}

/**
 * Перепрошить колонку Shift в указанном листе по shiftMap.
 * Возвращает { updated, total }.
 */
function syncShiftColumn_(ss, sheetName, shiftMap) {
  const sh = ss.getSheetByName(sheetName);
  if (!sh) return { updated: 0, total: 0 };
  const lastRow = sh.getLastRow();
  if (lastRow < 2) return { updated: 0, total: 0 };

  const nameCol  = findHeaderCol_(sh, 'Name');
  const shiftCol = findHeaderCol_(sh, 'Shift');
  if (!nameCol || !shiftCol) {
    Logger.log('syncShiftColumn_: %s — нет колонок Name/Shift, пропуск', sheetName);
    return { updated: 0, total: 0 };
  }

  // Если в D2 живёт formula (после миграции — не должна), удалим её
  // через clearContent на якорной ячейке, чтобы потом writeValues работал чисто.
  const anchorFormula = sh.getRange(2, shiftCol).getFormula();
  if (anchorFormula) {
    Logger.log('syncShiftColumn_: %s!D2 содержит формулу, очищаю перед записью: %s',
               sheetName, anchorFormula.substring(0, 80));
    sh.getRange(2, shiftCol, lastRow - 1, 1).clearContent();
  }

  const namesArr  = sh.getRange(2, nameCol,  lastRow - 1, 1).getValues();
  const shiftsArr = sh.getRange(2, shiftCol, lastRow - 1, 1).getValues();

  const out = [];
  let changed = 0;
  for (let i = 0; i < namesArr.length; i++) {
    const n = String(namesArr[i][0] || '').trim();
    const cur = String(shiftsArr[i][0] || '').trim();
    if (!n) {
      out.push(['']);
      if (cur !== '') changed++;
      continue;
    }
    const desired = shiftMap[normName_(n)] || '';
    out.push([desired]);
    if (desired !== cur) changed++;
  }

  if (changed > 0 || anchorFormula) {
    sh.getRange(2, shiftCol, out.length, 1).setValues(out);
  }
  return { updated: changed, total: namesArr.length };
}

/**
 * Обёртка для ручного запуска из GAS-редактора.
 * Сохранена как fixFormulas для обратной совместимости.
 */
function fixFormulas() {
  syncShiftsToDpAndDrivers_();
  Logger.log('fixFormulas: Shift синхронизирован из employees как plain text');
}

/**
 * Старое имя, оставлено для обратной совместимости со старыми триггерами.
 * Просто делегирует новой синхронизации.
 */
function ensureDriversAndPassengersFormulas_() {
  syncShiftsToDpAndDrivers_();
}

/**
 * Старая функция создания часового триггера. Сейчас Shift синхронизируется
 * через 5-минутный syncEmployeesAll, поэтому часовой триггер уже не нужен.
 * Если он есть — удалит. Если нет — ничего не сделает.
 */
function createFormulaGuardTrigger() {
  const functionName = 'ensureDriversAndPassengersFormulas_';
  const existing = ScriptApp.getProjectTriggers();
  existing.forEach(t => {
    if (t.getHandlerFunction() === functionName) {
      ScriptApp.deleteTrigger(t);
      Logger.log('createFormulaGuardTrigger: удалён устаревший часовой триггер');
    }
  });
}

/**
 * ОДНОРАЗОВАЯ миграция: удаляет ARRAYFORMULA в drivers!D2 и drivers_passengers!D2,
 * сохраняет текущие отображаемые значения как plain text.
 *
 * Запустить ОДИН РАЗ после деплоя этого патча. После этого 5-минутный
 * syncEmployeesAll будет поддерживать Shift в актуальном состоянии.
 */
function migrateShiftsToPlainText() {
  const ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  ['drivers', 'drivers_passengers'].forEach(function(name) {
    const sh = ss.getSheetByName(name);
    if (!sh) { Logger.log('migrate: лист %s не найден', name); return; }
    const lastRow = sh.getLastRow();
    if (lastRow < 2) { Logger.log('migrate: %s пустой', name); return; }

    const shiftCol = findHeaderCol_(sh, 'Shift');
    if (!shiftCol) { Logger.log('migrate: в %s не найдена колонка Shift', name); return; }

    // Сначала зафиксировать текущие значения (которые могут быть из формулы)
    const values = sh.getRange(2, shiftCol, lastRow - 1, 1).getValues();
    // Удалить формулу в anchor-ячейке (если она там есть как ARRAYFORMULA — это удалит и весь spill)
    sh.getRange(2, shiftCol, lastRow - 1, 1).clearContent();
    // Записать как plain-text
    sh.getRange(2, shiftCol, values.length, 1).setValues(values);
    Logger.log('migrate: %s — записано %d plain-text значений в Shift', name, values.length);
  });
  Logger.log('migrateShiftsToPlainText: готово. Теперь syncEmployeesAll будет поддерживать Shift автоматически.');
}

/************ RESTORE ALL TRIGGERS ************/
/**
 * Восстанавливает ВСЕ триггеры одним вызовом.
 * Безопасно вызывать повторно — каждый create* проверяет дубли.
 *
 * Запуск: Run → setupAllTriggers() в GAS-редакторе.
 */
function setupAllTriggers() {
  createDailyAppendTrigger();         // ежедневный snapshot в week1 (21:00)
  createWeeklyRotationTrigger();      // ротация week1→2→3→4 (воскресенье 22:00)
  createEmployeesEditTrigger();       // onEdit на employees
  createEmployeesTimeSyncTrigger();   // sync employees каждые 5 минут
  createFormulaGuardTrigger();        // формулы каждый час
  createShiftChangeQueueTrigger();    // обработка отложенных смен каждые 5 минут
  Logger.log('setupAllTriggers: все триггеры восстановлены.');
}

/************ REPORT HELPERS ************/

function parseMDY_(s) {
  var m = parseInt(s.substring(0, 2), 10) - 1;
  var d = parseInt(s.substring(2, 4), 10);
  var y = parseInt(s.substring(4, 8), 10);
  return new Date(y, m, d);
}

function fmtDate_(date, tz) {
  return Utilities.formatDate(date, tz, 'yyyy-MM-dd');
}

function fmtDateShort_(date, tz) {
  return Utilities.formatDate(date, tz, 'MM/dd');
}

function isSunday_(dateStr) {
  var parts = dateStr.split('-');
  var d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  return d.getDay() === 0;
}

/************ SMART NAME MATCHING ************/

/**
 * Dice coefficient on character bigrams. Returns 0.0–1.0.
 * Used as fuzzy fallback for typos (Ilyana ≈ Iliana).
 */
function diceSimilarity_(a, b) {
  if (a === b) return 1.0;
  if (a.length < 2 || b.length < 2) return 0.0;
  var bigramsA = {};
  for (var i = 0; i < a.length - 1; i++) {
    var bg = a.substring(i, i + 2);
    bigramsA[bg] = (bigramsA[bg] || 0) + 1;
  }
  var intersection = 0;
  for (var i = 0; i < b.length - 1; i++) {
    var bg = b.substring(i, i + 2);
    if (bigramsA[bg] && bigramsA[bg] > 0) {
      intersection++;
      bigramsA[bg]--;
    }
  }
  return (2.0 * intersection) / ((a.length - 1) + (b.length - 1));
}

/**
 * Build a resolver index from presenceMap keys.
 * Maps various name forms (sorted tokens, subsets) to canonical keys.
 * Enables O(1) lookup for reversed and partial names.
 */
function buildPresenceResolver_(presenceMap) {
  var resolver = {};
  var keys = Object.keys(presenceMap);

  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];

    // Exact
    resolver[key] = key;

    // Sorted tokens: "furkat achilov" and "achilov furkat" → same sorted key
    var tokens = key.split(/\s+/);
    var sorted = tokens.slice().sort().join(' ');
    if (!resolver[sorted]) resolver[sorted] = key;

    // Subsets: for 3+ word names, store with each token removed
    // "kantemir erali uulu" → "erali kantemir", "kantemir uulu", "erali uulu"
    if (tokens.length >= 3) {
      for (var j = 0; j < tokens.length; j++) {
        var subset = [];
        for (var k = 0; k < tokens.length; k++) {
          if (k !== j) subset.push(tokens[k]);
        }
        var subKey = subset.sort().join(' ');
        if (!resolver[subKey]) resolver[subKey] = key;
      }
    }
  }

  return resolver;
}

/**
 * Smart presence lookup. Tries in order:
 *   1. Exact match (O(1))
 *   2. Sorted-token match via resolver (O(1)) — handles reversed names
 *   3. Subset match via resolver (O(tokens)) — handles partial names
 *   4. Fuzzy match via Dice similarity ≥ 0.82 (O(n)) — handles typos
 */
function lookupPresence_(name, presenceMap, resolver, dateStr) {
  var norm = normName_(name);

  // 1. Exact
  if (presenceMap[norm] && presenceMap[norm][dateStr]) return true;

  // 2. Sorted tokens
  var tokens = norm.split(/\s+/);
  var sorted = tokens.slice().sort().join(' ');
  var resolved = resolver[sorted];
  if (resolved && presenceMap[resolved] && presenceMap[resolved][dateStr]) return true;

  // 3. Subset: try removing each token
  if (tokens.length >= 3) {
    for (var j = 0; j < tokens.length; j++) {
      var subset = [];
      for (var k = 0; k < tokens.length; k++) {
        if (k !== j) subset.push(tokens[k]);
      }
      var subKey = subset.sort().join(' ');
      resolved = resolver[subKey];
      if (resolved && presenceMap[resolved] && presenceMap[resolved][dateStr]) return true;
    }
  }

  // 4. Fuzzy fallback (for typos)
  var keys = Object.keys(presenceMap);
  var bestScore = 0;
  for (var i = 0; i < keys.length; i++) {
    if (!presenceMap[keys[i]][dateStr]) continue;
    var score = diceSimilarity_(sorted, keys[i].split(/\s+/).sort().join(' '));
    if (score > bestScore) bestScore = score;
  }
  if (bestScore >= 0.82) return true;

  return false;
}

/**
 * Find timesheet sheets by suffix (AMAZON/MELTECH) and verify date overlap
 * using actual dates from the sheet's header row 1 (columns C-I).
 *
 * Does NOT depend on sheet name format — only requires the name to end
 * with "AMAZON" or "MELTECH" (case-insensitive).
 */
function weekDatesFromName_(name) {
  // Извлекаем старт недели из имени листа с шаблоном MMDDYYYY-MMDDYYYY.
  // Возвращает массив из 7 Date (Пн..Вс) или null, если шаблона нет.
  // Приоритетнее строки 1: чинит листы со сломанной/скопированной шапкой
  // (Columbus, Amazon 0706 и т.п.). Префиксные имена без шаблона → null → откат.
  // Разделители внутри даты необязательны: подходит и 08032026-08092026,
  // и 08/03/2026-08/09/2026 (как в живой таблице), и через дефис.
  var m = String(name || '').match(/(\d{1,2})\D?(\d{1,2})\D?(\d{4})\s*-\s*\d{1,2}\D?\d{1,2}\D?\d{4}/);
  if (!m) return null;
  var mm = parseInt(m[1], 10), dd = parseInt(m[2], 10), yy = parseInt(m[3], 10);
  var start = new Date(yy, mm - 1, dd);
  if (isNaN(start.getTime())) return null;
  var out = [];
  for (var i = 0; i < 7; i++) out.push(new Date(yy, mm - 1, dd + i));
  return out;
}

function findTimesheetSheets_(ss, weekStartStr, weekEndStr, tz) {
  var result = [];
  var wStart = new Date(weekStartStr + 'T00:00:00');
  var wEnd   = new Date(weekEndStr + 'T23:59:59');

  ss.getSheets().forEach(function(sh) {
    var name = sh.getName().trim();

    // Check suffix: accepts variations of AMAZON and MELTECH (incl. typos like "MELTEH")
    // Works with prefixes like "PHASE 5 ..." because regex anchors to the end with \b.
    var locMatch = name.match(/\b(AMAZON|AMZN|MELTECH|MELTEH|MILTECH|MLT|COLUMBUS|CLMB|CBUS)\s*$/i);
    if (!locMatch) return;

    // Normalize location to canonical form
    var location = locMatch[1].toUpperCase();
    if (location === 'MELTEH' || location === 'MILTECH' || location === 'MLT') location = 'MELTECH';
    if (location === 'AMZN') location = 'AMAZON';
    if (location === 'CLMB' || location === 'CBUS') location = 'COLUMBUS';

    // Skip known non-timesheet sheets
    var lower = name.toLowerCase();
    if (lower === 'drivers' || lower === 'drivers_passengers' || lower === 'employees') return;

    // Даты: приоритет — диапазон недели из ИМЕНИ листа (MMDDYYYY-MMDDYYYY);
    // откат на строку 1, колонки C-I (для префиксных/нестандартных имён).
    var lastCol = sh.getLastColumn();
    if (lastCol < 3) return;
    var headerDates = weekDatesFromName_(name);
    if (!headerDates) {
      var readCols = Math.min(lastCol - 2, 7);
      headerDates = sh.getRange(1, 3, 1, readCols).getValues()[0];
    }

    // Find min and max dates in the header
    var minDate = null;
    var maxDate = null;
    for (var i = 0; i < headerDates.length; i++) {
      var d = headerDates[i];
      if (!(d instanceof Date) || isNaN(d.getTime())) continue;
      if (!minDate || d < minDate) minDate = d;
      if (!maxDate || d > maxDate) maxDate = d;
    }

    if (!minDate || !maxDate) return; // no valid dates found — not a timesheet

    // Check date range overlap
    if (minDate <= wEnd && maxDate >= wStart) {
      result.push({ sheet: sh, location: location });
      Logger.log('findTimesheetSheets_: matched "%s" (%s — %s)',
        name, fmtDate_(minDate, tz), fmtDate_(maxDate, tz));
    }
  });

  return result;
}

function buildPresenceMap_(timesheetInfos, tz) {
  var map = {};

  timesheetInfos.forEach(function(info) {
    var sheet = info.sheet;
    var lastRow = sheet.getLastRow();
    var lastCol = sheet.getLastColumn();
    if (lastRow < 3 || lastCol < 3) return;

    var headerDates = weekDatesFromName_(sheet.getName());
    if (!headerDates) headerDates = sheet.getRange(1, 3, 1, 7).getValues()[0];
    var dateKeys = [];

    for (var i = 0; i < 7; i++) {
      var d = headerDates[i];
      if (d instanceof Date && !isNaN(d.getTime())) {
        dateKeys.push(fmtDate_(d, tz));
      } else {
        dateKeys.push(null);
      }
    }

    var readCols = Math.min(lastCol, 9);
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

function calculateCreditsSplit_(snapshots, mainPres, colPres, adjustments, weekLabel) {
  // Правило зачёта (обе площадки): день водителю, если он ОТМЕТИЛСЯ в табеле
  // в этот день И у него в боте на этот день записано >= 2 пассажира.
  // Присутствие пассажиров в табеле НЕ проверяется.
  //
  // Привязка дня к площадке: день, где водитель есть в COLUMBUS-табеле, идёт
  // в Columbus-сводку (Columbus побеждает при пересечении); иначе — в основную
  // (Amazon/Meltech). Воскресный авто-зачёт (нет табелей) → в основную.
  var mainCredits = {}, colCredits = {};
  var mainAnoms = [], colAnoms = [];
  var dates = Object.keys(snapshots).sort();
  if (!dates.length) return { mainCredits: mainCredits, colCredits: colCredits, mainAnoms: mainAnoms, colAnoms: colAnoms };

  var passengerHistory = {};
  var mainResolver = buildPresenceResolver_(mainPres);
  var colResolver  = buildPresenceResolver_(colPres);

  // Воскресное исключение — только для основной площадки: если в вс нет
  // ни одной отметки в основных табелях, старый авто-зачёт (водитель = present).
  var mainDatesWithTs = {};
  Object.keys(mainPres).forEach(function(empKey) {
    Object.keys(mainPres[empKey]).forEach(function(d) { mainDatesWithTs[d] = true; });
  });

  function ensure(store, normD, name) {
    if (!store[normD]) store[normD] = { name: name, days: 0, details: {} };
  }

  dates.forEach(function(dateStr) {
    var sunday = isSunday_(dateStr);
    var mainSundayExempt = sunday && !mainDatesWithTs[dateStr];
    var dayDrivers = snapshots[dateStr];

    Object.keys(dayDrivers).forEach(function(normDriver) {
      var entry = dayDrivers[normDriver];

      var adjKey = dateStr + '|' + normDriver;
      if (adjustments[adjKey]) entry = adjustments[adjKey];

      var has2 = entry.passengers.length >= 2;
      var inCol  = lookupPresence_(entry.driver, colPres, colResolver, dateStr);
      var inMain = lookupPresence_(entry.driver, mainPres, mainResolver, dateStr);

      if (inCol) {
        // Columbus-день
        if (has2) {
          ensure(colCredits, normDriver, entry.driver);
          colCredits[normDriver].days++;
        } else {
          colAnoms.push({
            date: dateStr, type: 'COLUMBUS_NO_CARPOOL', driver: entry.driver,
            details: 'В Columbus-табеле, но карпула >= 2 нет', week: weekLabel,
          });
        }
      } else if (inMain || mainSundayExempt) {
        // Основной день (Amazon/Meltech)
        if (has2) {
          ensure(mainCredits, normDriver, entry.driver);
          mainCredits[normDriver].days++;
        }
      } else {
        // Есть карпул, но водителя нет ни в одном табеле в этот день
        if (has2) {
          mainAnoms.push({
            date: dateStr, type: 'DRIVER_NO_TIMESHEET', driver: entry.driver,
            details: 'В боте (карпул >= 2), но не в табеле', week: weekLabel,
          });
        }
      }

      // Целостность: пассажир ушёл к другому водителю (информ., в основные аномалии)
      entry.passengers.forEach(function(p) {
        var normP = normName_(p);
        if (passengerHistory[normP] && passengerHistory[normP].driver !== normDriver) {
          mainAnoms.push({
            date: dateStr, type: 'PASSENGER_SWITCHED', driver: entry.driver,
            details: p + ': был у ' + passengerHistory[normP].driverName + ', теперь у ' + entry.driver,
            week: weekLabel,
          });
        }
        passengerHistory[normP] = { driver: normDriver, driverName: entry.driver };
      });
    });
  });

  return { mainCredits: mainCredits, colCredits: colCredits, mainAnoms: mainAnoms, colAnoms: colAnoms };
}

// Superseded by calculateCreditsSplit_ (оставлено для истории; не вызывается).
function calculateCredits_(snapshots, presenceMap, adjustments, weekLabel) {
  var credits = {};
  var anomalies = [];
  var dates = Object.keys(snapshots).sort();
  if (!dates.length) return { credits: credits, anomalies: anomalies };

  var passengerHistory = {};
  var resolver = buildPresenceResolver_(presenceMap);

  // Dates for which the timesheet has any entry. When Sunday is covered by
  // the timesheet, it's verified like a weekday. When Sunday has no entries
  // at all (HR hasn't closed it yet), fall back to the legacy auto-credit.
  var datesWithTimesheet = {};
  Object.keys(presenceMap).forEach(function(empKey) {
    Object.keys(presenceMap[empKey]).forEach(function(d) {
      datesWithTimesheet[d] = true;
    });
  });

  dates.forEach(function(dateStr) {
    var sunday = isSunday_(dateStr);
    var sundayExempt = sunday && !datesWithTimesheet[dateStr];
    var dayDrivers = snapshots[dateStr];

    Object.keys(dayDrivers).forEach(function(normDriver) {
      var entry = dayDrivers[normDriver];

      var adjKey = dateStr + '|' + normDriver;
      if (adjustments[adjKey]) {
        entry = adjustments[adjKey];
      }

      if (!credits[normDriver]) {
        credits[normDriver] = { name: entry.driver, days: 0, details: {} };
      }

      var driverPresent = sundayExempt || lookupPresence_(entry.driver, presenceMap, resolver, dateStr);

      if (!driverPresent && entry.passengers.length > 0) {
        anomalies.push({
          date: dateStr, type: 'DRIVER_NO_TIMESHEET',
          driver: entry.driver,
          details: 'В боте, но не в табеле',
          week: weekLabel,
        });
        credits[normDriver].details[dateStr] = { credited: false, verified: 0, total: entry.passengers.length };
        return;
      }

      var verified = 0;
      var unverifiedNames = [];

      entry.passengers.forEach(function(p) {
        var normP = normName_(p);
        var pPresent = sundayExempt || lookupPresence_(p, presenceMap, resolver, dateStr);

        if (pPresent) {
          verified++;
        } else {
          unverifiedNames.push(p);
        }

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

      var credited = driverPresent && verified >= 2;
      credits[normDriver].details[dateStr] = { credited: credited, verified: verified, total: entry.passengers.length };

      if (credited) {
        credits[normDriver].days++;
      }

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

  // LATE_REGISTRATION detection
  var firstDate = dates[0];
  Object.keys(credits).forEach(function(normDriver) {
    var driverDates = Object.keys(credits[normDriver].details).sort();
    if (!driverDates.length || driverDates[0] === firstDate) return;

    var firstDriverDate = driverDates[0];
    var driverInTimesheetDay1 = presenceMap[normDriver] && presenceMap[normDriver][firstDate];
    if (!driverInTimesheetDay1) return;

    var firstSnap = snapshots[firstDriverDate] && snapshots[firstDriverDate][normDriver];
    if (!firstSnap || firstSnap.passengers.length === 0) return;

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

function writeSvodka_(ss, creditsWeekA, creditsWeekB, labelA, labelB, anomaliesA, anomaliesB, sheetName) {
  sheetName = sheetName || CONFIG.svodkaSheet;
  var sh = ss.getSheetByName(sheetName);
  if (!sh) sh = ss.insertSheet(sheetName);
  sh.clearContents();

  var driverAnomalySummary = {};
  function addSummary(anomalies, label) {
    anomalies.forEach(function(a) {
      var key = normName_(a.driver);
      if (!driverAnomalySummary[key]) driverAnomalySummary[key] = [];
      var tag = a.type + '|' + label;
      var existing = driverAnomalySummary[key].map(function(s) { return s.tag; });
      if (existing.indexOf(tag) === -1) {
        driverAnomalySummary[key].push({ tag: tag, text: a.type + ' ' + label });
      }
    });
  }
  addSummary(anomaliesA, labelA);
  addSummary(anomaliesB, labelB);

  var allDrivers = {};
  [creditsWeekA, creditsWeekB].forEach(function(credits) {
    Object.keys(credits).forEach(function(normD) {
      if (!allDrivers[normD]) allDrivers[normD] = credits[normD].name;
    });
  });

  var sortedDrivers = Object.keys(allDrivers).sort(function(a, b) {
    return allDrivers[a].localeCompare(allDrivers[b]);
  });

  var header = ['Водитель', labelA, labelB, 'Комментарий'];
  var rows = [header];

  sortedDrivers.forEach(function(normD) {
    var daysA = creditsWeekA[normD] ? creditsWeekA[normD].days : 0;
    var daysB = creditsWeekB[normD] ? creditsWeekB[normD].days : 0;
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

function withSpreadsheetRetry_(label, fn) {
  // Повтор при транзиентных сбоях сервиса Spreadsheets (таймаут/lock из-за
  // параллельных триггеров). До 3 попыток с нарастающей паузой.
  var lastErr;
  for (var attempt = 1; attempt <= 3; attempt++) {
    try {
      return fn();
    } catch (e) {
      lastErr = e;
      Logger.log('withSpreadsheetRetry_(%s): попытка %d не удалась: %s', label, attempt, e.message);
      if (attempt < 3) {
        SpreadsheetApp.flush();
        Utilities.sleep(3000 * attempt);
      }
    }
  }
  throw lastErr;
}

function writeAnomalies_(ss, anomalies, sheetName) {
  sheetName = sheetName || CONFIG.anomaliesSheet;
  var sh = ss.getSheetByName(sheetName);
  if (!sh) sh = ss.insertSheet(sheetName);

  // Очищаем и переписываем целиком (как writeSvodka_): лист отражает текущий
  // прогон, не растёт бесконечно и идемпотентен → безопасно повторить при сбое.
  sh.clearContents();

  var rows = [['Date', 'Type', 'Driver', 'Details', 'Week']];
  (anomalies || []).forEach(function(a) {
    rows.push([a.date, a.type, a.driver, a.details, a.week]);
  });

  // Одним вызовом setValues — меньше обращений к сервису, ниже риск таймаута.
  sh.getRange(1, 1, rows.length, 5).setValues(rows);
}

/************ MAIN 3: Bi-weekly report ************/

function generateBiWeeklyReport() {
  var ss = SpreadsheetApp.openByUrl(CONFIG.spreadsheetUrl);
  var tz = ss.getSpreadsheetTimeZone();

  var w3 = mustGetSheet_(ss, CONFIG.week3);
  var w2 = mustGetSheet_(ss, CONFIG.week2);

  var snapshotsA = getSnapshotsForWeek_(w3);
  var snapshotsB = getSnapshotsForWeek_(w2);

  var datesA = Object.keys(snapshotsA).sort();
  var datesB = Object.keys(snapshotsB).sort();

  if (!datesA.length && !datesB.length) {
    Logger.log('generateBiWeeklyReport: no snapshot data in week2/week3');
    return;
  }

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

  var tsA = startA ? findTimesheetSheets_(ss, startA, endA, tz) : [];
  var tsB = startB ? findTimesheetSheets_(ss, startB, endB, tz) : [];

  if (!tsA.length && datesA.length) {
    Logger.log('WARNING: no timesheet sheets found for week A (%s)', labelA);
  }
  if (!tsB.length && datesB.length) {
    Logger.log('WARNING: no timesheet sheets found for week B (%s)', labelB);
  }

  function isColumbus_(info) { return info.location === 'COLUMBUS'; }
  function isMainLoc_(info) { return info.location === 'AMAZON' || info.location === 'MELTECH'; }

  // Раздельные пулы присутствия: основной (Amazon+Meltech) и Columbus.
  var mainPresA = buildPresenceMap_(tsA.filter(isMainLoc_), tz);
  var colPresA  = buildPresenceMap_(tsA.filter(isColumbus_), tz);
  var mainPresB = buildPresenceMap_(tsB.filter(isMainLoc_), tz);
  var colPresB  = buildPresenceMap_(tsB.filter(isColumbus_), tz);

  Logger.log('generateBiWeeklyReport: main A=%d/B=%d, columbus A=%d/B=%d people',
    Object.keys(mainPresA).length, Object.keys(mainPresB).length,
    Object.keys(colPresA).length, Object.keys(colPresB).length);

  var globalStart = startA || startB;
  var globalEnd = endB || endA;
  var adjustments = getManualAdjustments_(ss, globalStart, globalEnd);

  var splitA = calculateCreditsSplit_(snapshotsA, mainPresA, colPresA, adjustments, labelA);
  var splitB = calculateCreditsSplit_(snapshotsB, mainPresB, colPresB, adjustments, labelB);

  // Основная сводка (Amazon/Meltech) + аномалии.
  // Записи обёрнуты в retry: таблицу параллельно трогают другие триггеры,
  // из-за чего сервис Spreadsheets иногда отдаёт таймаут. Обе write-функции
  // очищают лист перед записью, поэтому повтор безопасен (идемпотентен).
  withSpreadsheetRetry_('svodka main', function() {
    writeSvodka_(ss, splitA.mainCredits, splitB.mainCredits, labelA, labelB,
                 splitA.mainAnoms, splitB.mainAnoms, CONFIG.svodkaSheet);
  });
  withSpreadsheetRetry_('anomalies main', function() {
    writeAnomalies_(ss, splitA.mainAnoms.concat(splitB.mainAnoms), CONFIG.anomaliesSheet);
  });

  // Columbus — отдельная сводка + отдельные аномалии
  withSpreadsheetRetry_('svodka columbus', function() {
    writeSvodka_(ss, splitA.colCredits, splitB.colCredits, labelA, labelB,
                 splitA.colAnoms, splitB.colAnoms, CONFIG.svodkaColumbusSheet);
  });
  withSpreadsheetRetry_('anomalies columbus', function() {
    writeAnomalies_(ss, splitA.colAnoms.concat(splitB.colAnoms), CONFIG.anomaliesColumbusSheet);
  });

  Logger.log('generateBiWeeklyReport: done. main anomalies=%d, columbus anomalies=%d',
    splitA.mainAnoms.length + splitB.mainAnoms.length,
    splitA.colAnoms.length + splitB.colAnoms.length);
}

