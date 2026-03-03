/**
 * Google Apps Script для приёма данных из ТАБЕЛЬ
 *
 * Summary: Дата | Смена | Сотрудник | Check in | Check out | Объект | Часы
 * Часы — ARRAYFORMULA в G2 (создаётся автоматически)
 *
 * Смена по времени check-in: DAY_START..DAY_END → День, остальное → Ночь
 * Архив: 14 дней (очистка не чаще раза в сутки)
 *
 * ДИАГНОСТИКА: откройте URL деплоя в браузере — увидите тестовый результат.
 * Ошибки пишутся в лист "Debug".
 */

var SPREADSHEET_URL = 'https://docs.google.com/spreadsheets/d/1jZgVk4fJxw5bK9Pee5gxD9K35VK7x5cMMtu4lvdqtho/edit';
var HOURS_FORMULA = '=ARRAYFORMULA(IF((D2:D<>"")*(E2:E<>""); ROUNDUP(IF(E2:E<D2:D; (1+E2:E-D2:D)*24; (E2:E-D2:D)*24)); ""))';
var DAY_START = 7;
var DAY_END   = 19;

// ═══════════════════════════════════════════════════════════════
// ТЕСТ — откройте URL деплоя в браузере (GET-запрос)
// ═══════════════════════════════════════════════════════════════
function doGet(e) {
  try {
    var ss   = SpreadsheetApp.openByUrl(SPREADSHEET_URL);
    var tz   = Session.getScriptTimeZone();
    var now  = new Date();
    var dateStr = Utilities.formatDate(now, tz, 'dd.MM.yyyy');
    var timeStr = Utilities.formatDate(now, tz, 'HH:mm');

    var testData = {
      name:      '_ТЕСТ_',
      action:    'in',
      timestamp: now.getTime(),
      source:    'manual',
      site_id:   'test'
    };

    var results = [];
    results.push('Скрипт: НОВЫЙ (getShiftByTime)');
    results.push('Время: ' + dateStr + ' ' + timeStr);
    results.push('Timezone: ' + tz);

    // Тест Daily
    try {
      writeDaily(ss, testData, dateStr, timeStr);
      results.push('writeDaily: OK');
    } catch (err) {
      results.push('writeDaily: ОШИБКА — ' + err.message);
    }

    // Тест Summary
    try {
      updateSummary(ss, testData, dateStr, timeStr, now, tz);
      results.push('updateSummary: OK');
    } catch (err) {
      results.push('updateSummary: ОШИБКА — ' + err.message);
    }

    return ContentService
      .createTextOutput(results.join('\n'))
      .setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return ContentService
      .createTextOutput('КРИТИЧЕСКАЯ ОШИБКА: ' + err.message)
      .setMimeType(ContentService.MimeType.TEXT);
  }
}

// ═══════════════════════════════════════════════════════════════
// POST — основной вход от Табель
// ═══════════════════════════════════════════════════════════════
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);

    if (!data.name || !data.action || !data.timestamp) {
      return jsonResponse({ status: 'error', message: 'Missing required fields' });
    }
    if (data.action !== 'in' && data.action !== 'out') {
      return jsonResponse({ status: 'error', message: 'Invalid action' });
    }

    var ss   = SpreadsheetApp.openByUrl(SPREADSHEET_URL);
    var tz   = Session.getScriptTimeZone();
    var date = new Date(data.timestamp);

    if (isNaN(date.getTime())) {
      return jsonResponse({ status: 'error', message: 'Invalid timestamp' });
    }

    var dateStr = Utilities.formatDate(date, tz, 'dd.MM.yyyy');
    var timeStr = Utilities.formatDate(date, tz, 'HH:mm');

    // Daily — отдельный try/catch
    try {
      writeDaily(ss, data, dateStr, timeStr);
    } catch (err) {
      logError(ss, 'writeDaily', err);
    }

    // Summary — отдельный try/catch
    try {
      updateSummary(ss, data, dateStr, timeStr, date, tz);
    } catch (err) {
      logError(ss, 'updateSummary', err);
    }

    // Cleanup
    try {
      cleanupIfNeeded(ss);
    } catch (err) {
      logError(ss, 'cleanup', err);
    }

    return jsonResponse({ status: 'ok' });
  } catch (err) {
    return jsonResponse({ status: 'error', message: err.message });
  }
}

// ═══════════════════════════════════════════════════════════════
// Логирование ошибок в лист "Debug"
// ═══════════════════════════════════════════════════════════════
function logError(ss, funcName, err) {
  try {
    var sheet = ss.getSheetByName('Debug');
    if (!sheet) {
      sheet = ss.insertSheet('Debug');
      sheet.appendRow(['Время', 'Функция', 'Ошибка']);
      sheet.getRange(1, 1, 1, 3).setFontWeight('bold');
    }
    var tz = Session.getScriptTimeZone();
    var now = Utilities.formatDate(new Date(), tz, 'dd.MM.yyyy HH:mm:ss');
    sheet.appendRow([now, funcName, err.message + ' | ' + err.stack]);
  } catch (e) {
    // Если даже логирование упало — ничего не можем сделать
  }
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ═══════════════════════════════════════════════════════════════
// Вспомогательные функции
// ═══════════════════════════════════════════════════════════════

function normalizeName(name) {
  return name.toString().trim().replace(/\s+/g, ' ').toLowerCase();
}

function getShiftByTime(date, tz) {
  var hour = parseInt(Utilities.formatDate(date, tz, 'HH'), 10);
  return (hour >= DAY_START && hour < DAY_END) ? 'День' : 'Ночь';
}

function getSourceText(source) {
  if (source === 'scan')   return 'Скан';
  if (source === 'auto')   return 'Авто';
  if (source === 'manual') return 'Вручную';
  return source || '';
}

// ═══════════════════════════════════════════════════════════════
// Daily
// ═══════════════════════════════════════════════════════════════
function writeDaily(ss, data, dateStr, timeStr) {
  var sheet = ss.getSheetByName('Daily');
  if (!sheet) {
    sheet = ss.insertSheet('Daily');
    sheet.appendRow(['Дата', 'Время', 'Сотрудник', 'Действие', 'Источник', 'Объект']);
    sheet.getRange(1, 1, 1, 6).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  sheet.appendRow([
    dateStr,
    timeStr,
    data.name,
    data.action === 'in' ? 'Приход' : 'Уход',
    getSourceText(data.source),
    data.site_id || ''
  ]);
}

// ═══════════════════════════════════════════════════════════════
// Summary
// ═══════════════════════════════════════════════════════════════
function updateSummary(ss, data, dateStr, timeStr, date, tz) {
  var sheet = ss.getSheetByName('Summary');
  if (!sheet) {
    sheet = ss.insertSheet('Summary');
    sheet.appendRow(['Дата', 'Смена', 'Сотрудник', 'Check in', 'Check out', 'Объект', 'Часы']);
    sheet.getRange(1, 1, 1, 7).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }

  var normalizedInput = normalizeName(data.name);

  if (data.action === 'in') {
    var shift = getShiftByTime(date, tz);
    sheet.appendRow([dateStr, shift, data.name, timeStr, '', data.site_id || '']);

    if (sheet.getLastRow() === 2) {
      sheet.getRange(2, 7).setFormula(HOURS_FORMULA);
    }

  } else if (data.action === 'out') {
    var lastRow = sheet.getLastRow();
    if (lastRow < 2) return;

    var allData = sheet.getRange(2, 1, lastRow - 1, 6).getDisplayValues();
    for (var i = allData.length - 1; i >= 0; i--) {
      if (normalizeName(allData[i][2]) === normalizedInput && allData[i][4].trim() === '') {
        sheet.getRange(i + 2, 5).setValue(timeStr);
        break;
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// Очистка (> 14 дней, не чаще раза в сутки)
// ═══════════════════════════════════════════════════════════════
function cleanupIfNeeded(ss) {
  var props = PropertiesService.getScriptProperties();
  var last  = props.getProperty('lastCleanup');
  var now   = new Date().getTime();

  if (last && (now - parseInt(last, 10)) < 86400000) return;

  var cutoff = new Date(now - 14 * 24 * 60 * 60 * 1000);
  cleanSheet(ss.getSheetByName('Summary'), cutoff, true);
  cleanSheet(ss.getSheetByName('Daily'),   cutoff, false);

  props.setProperty('lastCleanup', now.toString());
}

function cleanSheet(sheet, cutoff, hasFormula) {
  if (!sheet) return;
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  var dates = sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues();

  var oldCount = 0;
  for (var i = 0; i < dates.length; i++) {
    var parts = dates[i][0].split('.');
    if (parts.length !== 3) break;
    var rowDate = new Date(parseInt(parts[2]), parseInt(parts[1]) - 1, parseInt(parts[0]));
    if (rowDate < cutoff) {
      oldCount = i + 1;
    } else {
      break;
    }
  }

  if (oldCount === 0) return;

  var formula = '';
  if (hasFormula) {
    formula = sheet.getRange(2, 7).getFormula() || HOURS_FORMULA;
  }

  sheet.deleteRows(2, oldCount);

  if (formula && sheet.getLastRow() >= 2) {
    sheet.getRange(2, 7).setFormula(formula);
  }
}
