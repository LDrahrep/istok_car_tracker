/**
 * Google Apps Script для приёма данных из ТАБЕЛЬ
 * (привязан к таблице — использует getActiveSpreadsheet)
 *
 * Summary: Дата | Смена | Сотрудник | Check in | Check out | Объект | Часы
 * Daily:   Дата | Время | Сотрудник | Действие | Источник | Объект
 *
 * Смена по времени: 07:00–19:00 → День, остальное → Ночь
 * Архив: 14 дней (очистка раз в сутки)
 */

var HOURS_FORMULA = '=ARRAYFORMULA(IF((D2:D<>"")*(E2:E<>""); ROUNDUP(IF(E2:E<D2:D; (1+E2:E-D2:D)*24; (E2:E-D2:D)*24)); ""))';
var DAY_START = 7;
var DAY_END   = 19;
var TZ        = 'America/Chicago';

// ═══════════════════════════════════════════════════════════════
// ТЕСТ — откройте URL деплоя в браузере
// ═══════════════════════════════════════════════════════════════
function doGet(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var results = [];

    // Диагностика времени
    var now = new Date();
    results.push('GAS сейчас (Chicago): ' + Utilities.formatDate(now, 'America/Chicago', 'MM.dd.yyyy HH:mm:ss'));
    results.push('GAS сейчас (UTC):     ' + Utilities.formatDate(now, 'UTC', 'MM.dd.yyyy HH:mm:ss'));
    results.push('Date.now() ms:        ' + now.getTime());
    results.push('');
    results.push('Spreadsheet: ' + ss.getName());
    results.push('URL: ' + ss.getUrl());

    // Все листы
    var sheets = ss.getSheets();
    results.push('Листы: ' + sheets.map(function(s) { return s.getName(); }).join(', '));

    // Summary — прямая запись
    var summary = ss.getSheetByName('Summary');
    if (!summary) {
      results.push('Summary: НЕ НАЙДЕН!');
    } else {
      results.push('Summary найден, lastRow: ' + summary.getLastRow());

      // Пишем напрямую через setValue (не appendRow)
      var row = summary.getLastRow() + 1;
      summary.getRange(row, 1).setValue('03.03.2026');  // MM.dd.yyyy
      summary.getRange(row, 2).setValue('День');
      summary.getRange(row, 3).setValue('_ДИАГНОСТИКА_');
      summary.getRange(row, 4).setValue('15:00');
      summary.getRange(row, 5).setValue('');
      summary.getRange(row, 6).setValue('test');

      SpreadsheetApp.flush();  // принудительная запись

      results.push('Записано в строку: ' + row);
      results.push('Проверка A' + row + ': ' + summary.getRange(row, 1).getValue());
      results.push('Проверка C' + row + ': ' + summary.getRange(row, 3).getValue());
      results.push('lastRow после: ' + summary.getLastRow());
    }

    return ContentService
      .createTextOutput(results.join('\n'))
      .setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return ContentService
      .createTextOutput('ОШИБКА: ' + err.message + '\n' + err.stack)
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

    var ss   = SpreadsheetApp.getActiveSpreadsheet();
    var date = new Date(data.timestamp);

    if (isNaN(date.getTime())) {
      return jsonResponse({ status: 'error', message: 'Invalid timestamp' });
    }

    var dateStr = Utilities.formatDate(date, TZ, 'MM.dd.yyyy');
    var timeStr = Utilities.formatDate(date, TZ, 'HH:mm');

    // Daily — отдельный try/catch чтобы ошибка не блокировала Summary
    try {
      writeDaily(ss, data, dateStr, timeStr);
    } catch (err) {
      logError(ss, 'writeDaily', err);
    }

    // Summary — отдельный try/catch
    try {
      updateSummary(ss, data, dateStr, timeStr, date);
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
    var now = Utilities.formatDate(new Date(), TZ, 'MM.dd.yyyy HH:mm:ss');
    sheet.appendRow([now, funcName, err.message]);
  } catch (e) { /* ignore */ }
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ═══════════════════════════════════════════════════════════════
// Вспомогательные
// ═══════════════════════════════════════════════════════════════
function normalizeName(name) {
  return name.toString().trim().replace(/\s+/g, ' ').toLowerCase();
}

function getShiftByTime(date) {
  var hour = parseInt(Utilities.formatDate(date, TZ, 'HH'), 10);
  return (hour >= DAY_START && hour < DAY_END) ? 'День' : 'Ночь';
}

function getSourceText(source) {
  if (source === 'scan')   return 'Скан';
  if (source === 'auto')   return 'Авто';
  if (source === 'manual') return 'Вручную';
  return source || '';
}

// ═══════════════════════════════════════════════════════════════
// Daily — сырой лог
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
// Summary — сводка по сменам
// ═══════════════════════════════════════════════════════════════
function updateSummary(ss, data, dateStr, timeStr, date) {
  var sheet = ss.getSheetByName('Summary');
  if (!sheet) {
    sheet = ss.insertSheet('Summary');
    sheet.appendRow(['Дата', 'Смена', 'Сотрудник', 'Check in', 'Check out', 'Объект', 'Часы']);
    sheet.getRange(1, 1, 1, 7).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }

  var normalizedInput = normalizeName(data.name);

  if (data.action === 'in') {
    var shift = getShiftByTime(date);
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
// Очистка (> 14 дней, раз в сутки)
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
    var rowDate = new Date(parseInt(parts[2]), parseInt(parts[0]) - 1, parseInt(parts[1]));
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
