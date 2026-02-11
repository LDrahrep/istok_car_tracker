# =========================
# TELEGRAM DRIVER BOT
# Memphis, TN (America/Chicago)
# =========================

import os
import logging
import difflib
from datetime import time, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional
import os, json
from google.oauth2.service_account import Credentials

import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# =========================
# CONFIG
# =========================

TIMEZONE = "America/Chicago"
DAY_SHIFT_TIME = "07:00"
NIGHT_SHIFT_TIME = "19:00"

ADMIN_USERS = {1270793968}

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "service_account.json")

DRIVERS_SHEET = "drivers"
EMPLOYEES_SHEET = "employees"
DRIVERS_PASSENGERS_SHEET = "drivers_passengers"

# =========================
# LOGGING
# =========================

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# =========================
# GOOGLE SHEETS
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = Credentials.from_service_account_info(info, scopes=SCOPES)

gc = gspread.authorize(creds)


def ws(name):
    return gc.open_by_key(SPREADSHEET_ID).worksheet(name)

# =========================
# KEYBOARD
# =========================

BTN_ADD = "🚗 Добавить/обновить водителя"
BTN_PASS = "👥 Указать пассажиров"
BTN_DEL = "🗑 Удалить пассажира"
BTN_MY = "📄 Моя запись"
BTN_CANCEL = "❌ Отмена"
BTN_SHUT = "🛑 Shutdown"

def menu(is_admin=False):
    rows = [
        [KeyboardButton(BTN_ADD)],
        [KeyboardButton(BTN_PASS)],
        [KeyboardButton(BTN_DEL)],
        [KeyboardButton(BTN_MY)],
        [KeyboardButton(BTN_CANCEL)],
    ]
    if is_admin:
        rows.append([KeyboardButton(BTN_SHUT)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выберите действие кнопками 👇",
        reply_markup=menu(update.effective_user.id in ADMIN_USERS),
    )

# =========================
# HELPERS
# =========================

def norm(s): 
    return (s or "").strip().lower()

def normalize_shift_value(s: str) -> str:
    t = (s or "").strip().lower()
    if "night" in t or "ноч" in t:
        return "night"
    if "day" in t or "дн" in t:
        return "day"
    return ""

def parse_time(hhmm):
    h, m = hhmm.split(":")
    return time(int(h), int(m), tzinfo=ZoneInfo(TIMEZONE))
# =========================
# DRIVER LOGIC
# =========================

def get_driver(tg_id):
    sheet = ws(DRIVERS_SHEET)
    data = sheet.get_all_records()
    for i, row in enumerate(data, start=2):
        if str(row.get("telegramID")) == str(tg_id):
            return row, i
    return None, None

def upsert_driver(tg_id: int, name: str, phone: str, shift: str, car: str, plates: str):
    """
    Обновляет строку водителя по telegramID, иначе добавляет новую.
    Ожидаемые колонки drivers:
      A Name | B telegramID | C Phone number | D Shift | E Car | F Plates | G isActive
    """
    sheet = ws(DRIVERS_SHEET)
    values = sheet.get_all_values()
    if not values:
        raise RuntimeError("Лист drivers пустой (нет заголовков).")

    headers = values[0]
    h = {norm(x): i for i, x in enumerate(headers)}

    def col(name1, *alts):
        for k in (name1, *alts):
            if norm(k) in h:
                return h[norm(k)]
        return None

    c_name = col("Name")
    c_tg = col("telegramID", "telegramid")
    c_phone = col("Phone number", "phonenumber", "phone")
    c_shift = col("Shift")
    c_car = col("Car")
    c_plates = col("Plates")
    c_active = col("isActive", "isactive")

    if c_name is None or c_tg is None:
        raise RuntimeError("В drivers должны быть колонки минимум: Name и telegramID")

    # ищем строку по tg_id
    row_idx = None
    for i, row in enumerate(values[1:], start=2):
        if c_tg < len(row) and row[c_tg].strip() == str(tg_id):
            row_idx = i
            break

    def set_cell(r, c0, v):
        if c0 is None:
            return
        sheet.update_cell(r, c0 + 1, v)

    if row_idx:
        set_cell(row_idx, c_name, name)
        set_cell(row_idx, c_tg, str(tg_id))
        set_cell(row_idx, c_phone, phone)
        set_cell(row_idx, c_shift, shift)
        set_cell(row_idx, c_car, car)
        set_cell(row_idx, c_plates, plates)
        set_cell(row_idx, c_active, "TRUE")
        return False, row_idx

    # если не нашли — добавляем новую
    row = [""] * len(headers)
    row[c_name] = name
    row[c_tg] = str(tg_id)
    if c_phone is not None: row[c_phone] = phone
    if c_shift is not None: row[c_shift] = shift
    if c_car is not None: row[c_car] = car
    if c_plates is not None: row[c_plates] = plates
    if c_active is not None: row[c_active] = "TRUE"

    sheet.append_row(row, value_input_option="USER_ENTERED")
    return True, len(values) + 1


def add_driver_self_to_employees(name, tg_id):
    """
    Если сотрудник уже есть — заполняем только D и E.
    Если нет — создаём новую строку, заполняя только A, D, E.
    """
    sheet = ws(EMPLOYEES_SHEET)
    data = sheet.get_all_records()

    for i, row in enumerate(data, start=2):
        if norm(row.get("Employee")) == norm(name):
            sheet.update_cell(i, 4, name)          # D = Rides with
            sheet.update_cell(i, 5, str(tg_id))    # E = Driver's TGID
            return

    # если не найден — создаём новую строку
    sheet.append_row([name, "", "", name, str(tg_id)])


# =========================
# ADD DRIVER FLOW
# =========================

ADD_NAME, CONFIRM_PHONE, ADD_SHIFT, ADD_CAR, ADD_PLATES = range(5)


async def add_driver_start(update, context):
    context.user_data.clear()
    await update.message.reply_text("Введите имя работника:")
    return ADD_NAME


async def add_driver_name(update, context):
    name = update.message.text.strip()
    context.user_data["name"] = name

    sheet = ws(EMPLOYEES_SHEET)
    data = sheet.get_all_records()

    for row in data:
        if norm(row.get("Employee")) == norm(name):
            phone = row.get("PhoneNumber")
            if not phone:
                await update.message.reply_text(
                    "Телефон у сотрудника отсутствует. Обратитесь к менеджеру."
                )
                return ConversationHandler.END

            context.user_data["phone"] = phone
            context.user_data["shift"] = row.get("Shift", "")

            await update.message.reply_text(
                f"Найден номер: {phone}\nЭто правильный номер?\n\nНапишите: Да или Нет"
            )
            return CONFIRM_PHONE

    await update.message.reply_text(
        "Сотрудник не найден в таблице employees.\nОбратитесь к менеджеру."
    )
    return ConversationHandler.END


async def confirm_phone(update, context):
    answer = update.message.text.strip().lower()

    if answer != "да":
        await update.message.reply_text("Запись не создана. Обратитесь к менеджеру.")
        return ConversationHandler.END

    # дальше продолжаем диалог
    await update.message.reply_text("Введите Shift (Day или Night):")
    return ADD_SHIFT

async def add_driver_shift(update, context):
    shift = (update.message.text or "").strip()
    if not shift:
        await update.message.reply_text("Shift пустой. Введите Shift (Day/Night):")
        return ADD_SHIFT
    context.user_data["shift_manual"] = shift
    await update.message.reply_text("Введите Car:")
    return ADD_CAR


async def add_driver_car(update, context):
    car = (update.message.text or "").strip()
    if not car:
        await update.message.reply_text("Car пустой. Введите Car:")
        return ADD_CAR
    context.user_data["car"] = car
    await update.message.reply_text("Введите Plates:")
    return ADD_PLATES


async def add_driver_plates(update, context):
    plates = (update.message.text or "").strip()
    if not plates:
        await update.message.reply_text("Plates пустой. Введите Plates:")
        return ADD_PLATES

    name = context.user_data.get("name")
    phone = context.user_data.get("phone")
    shift = context.user_data.get("shift_manual") or context.user_data.get("shift") or ""
    car = context.user_data.get("car", "")
    tg_id = update.effective_user.id

    if not name or not phone or not shift:
        await update.message.reply_text("Диалог сбился. Начните заново.")
        return ConversationHandler.END

    created, row_idx = upsert_driver(
        tg_id=tg_id,
        name=name,
        phone=phone,
        shift=shift,
        car=car,
        plates=plates,
    )

    add_driver_self_to_employees(name, tg_id)

    if created:
        await update.message.reply_text(f"✅ Водитель добавлен (строка {row_idx})")
    else:
        await update.message.reply_text(f"✅ Водитель обновлён (строка {row_idx})")

    return ConversationHandler.END

# =========================
# PASSENGERS LOGIC
# =========================

PASS_INPUT = 10

async def passengers_start(update, context):
    await update.message.reply_text(
        "Введите имена пассажиров (до 4) через запятую или с новой строки:\n\n"
        "Пример:\nИван Иванов, Пётр Петров\n\n"
        "или:\nИван Иванов\nПётр Петров"
    )
    return PASS_INPUT


async def passengers_input(update, context):
    driver, _ = get_driver(update.effective_user.id)
    if not driver:
        await update.message.reply_text("Вы не водитель. Сначала добавьте себя.")
        return ConversationHandler.END

    driver_name = driver.get("Name", "")
    driver_shift = driver.get("Shift", "")
    driver_shift_norm = normalize_shift_value(driver_shift)
    driver_tg = str(update.effective_user.id)

    raw = (update.message.text or "").strip()
    names = [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]

    # убираем дубли
    uniq = []
    seen = set()
    for n in names:
        k = norm(n)
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    names = uniq

    if not names:
        await update.message.reply_text("Пусто. Введите имена.")
        return PASS_INPUT

    if len(names) > 4:
        await update.message.reply_text("Максимум 4 пассажира.")
        return PASS_INPUT

    emp_sheet = ws(EMPLOYEES_SHEET)
    emp_data = emp_sheet.get_all_records()

    # построим быстрый индекс по employees
    emp_index = {}  # norm(name) -> (row_number, row_dict)
    for idx, row in enumerate(emp_data, start=2):
        emp_name = row.get("Employee", "")
        if emp_name:
            emp_index[norm(emp_name)] = (idx, row)

    # 1) Проверка: все имена существуют
    # 2) Проверка: shift совпадает с shift водителя
    # 3) Проверка: пассажир не закреплён за другим водителем
    valid_rows = []

    for passenger in names:
        key = norm(passenger)

        if key not in emp_index:
            await update.message.reply_text(
                f"Пассажир '{passenger}' не найден в employees. Проверьте написание."
            )
            return ConversationHandler.END

        row_num, row = emp_index[key]

        # SHIFT CHECK
        p_shift = row.get("Shift", "")
        p_shift_norm = normalize_shift_value(p_shift)

        if driver_shift_norm and p_shift_norm and (driver_shift_norm != p_shift_norm):
            await update.message.reply_text("Смены не совпадают,  обратитесь к менеджеру")
            return ConversationHandler.END

        # EXCLUSIVITY CHECK
        existing_tgid = str(row.get("Driver's TGID", "")).strip()
        existing_rides = str(row.get("Rides with", "")).strip()

        if existing_tgid and existing_tgid != driver_tg:
            await update.message.reply_text(
                f"⛔ Пассажир '{passenger}' уже закреплён за другим водителем.\n"
                "Обратитесь к менеджеру."
            )
            return ConversationHandler.END

        if (not existing_tgid) and existing_rides and norm(existing_rides) != norm(driver_name):
            await update.message.reply_text(
                f"⛔ Пассажир '{passenger}' уже закреплён за другим водителем.\n"
                "Обратитесь к менеджеру."
            )
            return ConversationHandler.END

        valid_rows.append((passenger, row_num))

    # записываем в drivers_passengers
    dp = ws(DRIVERS_PASSENGERS_SHEET)
    dp.append_row([
        driver.get("Name", ""),
        driver.get("telegramID", ""),
        driver.get("Phone number", ""),
        driver.get("Shift", ""),
        *(names + [""] * (4 - len(names)))
    ])

    # обновляем employees (ТОЛЬКО D и E)
    for passenger, row_num in valid_rows:
        emp_sheet.update_cell(row_num, 4, driver_name)   # D = Rides with
        emp_sheet.update_cell(row_num, 5, driver_tg)     # E = Driver's TGID

    # водитель должен быть приписан к себе (D/E only)
    add_driver_self_to_employees(driver_name, int(driver_tg))

    await update.message.reply_text("✅ Пассажиры добавлены.")
    return ConversationHandler.END

# =========================
# DELETE PASSENGER
# =========================

DEL_INPUT = 20

async def delete_start(update, context):
    driver, _ = get_driver(update.effective_user.id)
    if not driver:
        await update.message.reply_text("Вы не водитель. Сначала добавьте себя.")
        return ConversationHandler.END

    dp = ws(DRIVERS_PASSENGERS_SHEET)
    rows = dp.get_all_records()

    driver_tg = str(update.effective_user.id)

    # найдём строку этого водителя по TGID
    row_idx = None
    passengers = []
    for i, row in enumerate(rows, start=2):
        if str(row.get("TGID")) == driver_tg:
            row_idx = i
            passengers = [
                row.get("Passenger1", ""),
                row.get("Passenger2", ""),
                row.get("Passenger3", ""),
                row.get("Passenger4", ""),
            ]
            passengers = [p for p in passengers if p]
            break

    if not row_idx:
        await update.message.reply_text("У вас нет записи в drivers_passengers.")
        return ConversationHandler.END

    if not passengers:
        await update.message.reply_text("У вас нет пассажиров для удаления.")
        return ConversationHandler.END

    context.user_data["dp_row_idx"] = row_idx
    context.user_data["passengers"] = passengers

    await update.message.reply_text(
        "Ваши пассажиры:\n"
        + "\n".join([f"- {p}" for p in passengers])
        + "\n\nВведите имя пассажира для удаления:"
    )
    return DEL_INPUT


async def delete_input(update, context):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Пусто. Введите имя пассажира:")
        return DEL_INPUT

    passengers = context.user_data.get("passengers", [])
    row_idx = context.user_data.get("dp_row_idx")

    if not row_idx or not passengers:
        await update.message.reply_text("Диалог сбился. Нажмите кнопку «Удалить пассажира» ещё раз.")
        return ConversationHandler.END

    # проверка правильности ввода
    if norm(name) not in {norm(p) for p in passengers}:
        await update.message.reply_text(
            "Пассажир не найден в вашем списке. Введите точное имя ещё раз."
        )
        return DEL_INPUT

    dp = ws(DRIVERS_PASSENGERS_SHEET)
    row_vals = dp.row_values(row_idx)

    # cols: A Name, B TGID, C Phone Number, D Shift, E..H Passenger1..4
    target_col = None
    for col in range(5, 9):  # E=5..H=8
        if col - 1 < len(row_vals) and norm(row_vals[col - 1]) == norm(name):
            target_col = col
            break

    if not target_col:
        await update.message.reply_text("Не смог найти ячейку пассажира. Попробуйте ещё раз.")
        return DEL_INPUT

    # удаляем пассажира в drivers_passengers
    dp.update_cell(row_idx, target_col, "")

    # открепляем водителя в employees (ТОЛЬКО если текущий водитель закреплён)
    emp = ws(EMPLOYEES_SHEET)
    emp_vals = emp.get_all_values()
    my_tg = str(update.effective_user.id)

    for i, row in enumerate(emp_vals[1:], start=2):
        emp_name = row[0].strip() if len(row) >= 1 else ""
        if norm(emp_name) == norm(name):
            cur_tgid = row[4].strip() if len(row) >= 5 else ""
            if cur_tgid == my_tg:
                emp.update_cell(i, 4, "")  # D
                emp.update_cell(i, 5, "")  # E
            break

    await update.message.reply_text("✅ Пассажир удалён.")
    return ConversationHandler.END

# =========================
# DAILY CONFIRM (YES/NO) + AUTO CLEAR
# =========================

pending_confirmations = {}  # tg_id -> {"job": job, "shift_kind": "day|night"}

async def daily_ask_driver(context: ContextTypes.DEFAULT_TYPE):
    """
    Рассылает всем водителям вопрос: "всё ещё с теми же пассажирами?"
    shift_kind приходит в context.job.data: "day" or "night"
    """
    shift_kind = context.job.data

    drv_sheet = ws(DRIVERS_SHEET)
    drivers = drv_sheet.get_all_records()

    dp_sheet = ws(DRIVERS_PASSENGERS_SHEET)
    dp_rows = dp_sheet.get_all_records()

    for d in drivers:
        tg_id = d.get("telegramID")
        if not tg_id:
            continue

        tg_id = int(tg_id)

        # фильтр по смене
        driver_shift_kind = normalize_shift_value(d.get("Shift", ""))
        if driver_shift_kind and driver_shift_kind != shift_kind:
            continue

        # получить пассажиров из drivers_passengers
        passengers = []
        dp_row_idx = None
        for i, row in enumerate(dp_rows, start=2):
            if str(row.get("TGID")) == str(tg_id):
                dp_row_idx = i
                passengers = [
                    row.get("Passenger1", ""),
                    row.get("Passenger2", ""),
                    row.get("Passenger3", ""),
                    row.get("Passenger4", ""),
                ]
                passengers = [p for p in passengers if p]
                break

        txt = "Ежедневная проверка 🚘\n\n"
        txt += "Текущие пассажиры:\n"
        if passengers:
            txt += "\n".join([f"• {p}" for p in passengers])
        else:
            txt += "— (пассажиров нет)"
        txt += "\n\nТы всё ещё возишь этих же людей?\nОтветь: Да или Нет\n"
        txt += "Если не ответишь за 60 минут — запись будет очищена."

        try:
            await context.bot.send_message(chat_id=tg_id, text=txt)
        except Exception:
            continue

        # ставим авто-очистку через 60 минут
        if tg_id in pending_confirmations:
            try:
                pending_confirmations[tg_id]["job"].schedule_removal()
            except Exception:
                pass

        job = context.job_queue.run_once(
            daily_timeout_clear,
            when=timedelta(minutes=60),
            data={"tg_id": tg_id},
            name=f"daily_clear_{tg_id}",
        )
        pending_confirmations[tg_id] = {"job": job, "shift_kind": shift_kind}


async def daily_timeout_clear(context: ContextTypes.DEFAULT_TYPE):
    """
    Если водитель не ответил за 60 минут — стираем пассажиров и открепляем в employees.
    """
    tg_id = int(context.job.data["tg_id"])

    # если к этому времени водитель уже ответил — ignore
    if tg_id not in pending_confirmations:
        return

    pending_confirmations.pop(tg_id, None)

    # очистка passengers в drivers_passengers
    dp = ws(DRIVERS_PASSENGERS_SHEET)
    dp_vals = dp.get_all_values()

    passengers_to_detach = []

    for i, row in enumerate(dp_vals[1:], start=2):
        if len(row) >= 2 and row[1].strip() == str(tg_id):
            # пассажиры E..H
            for c in range(4, 8):
                if len(row) > c and row[c].strip():
                    passengers_to_detach.append(row[c].strip())
            # очищаем E..H
            for col in range(5, 9):
                dp.update_cell(i, col, "")
            break

    # открепляем в employees (D/E)
    emp = ws(EMPLOYEES_SHEET)
    emp_vals = emp.get_all_values()

    for p in passengers_to_detach:
        for j, erow in enumerate(emp_vals[1:], start=2):
            emp_name = erow[0].strip() if len(erow) >= 1 else ""
            if norm(emp_name) == norm(p):
                cur_tgid = erow[4].strip() if len(erow) >= 5 else ""
                if cur_tgid == str(tg_id):
                    emp.update_cell(j, 4, "")
                    emp.update_cell(j, 5, "")
                break

    try:
        await context.bot.send_message(
            chat_id=tg_id,
            text="⏰ 60 минут прошло — я очистил запись пассажиров. Если нужно — укажи заново кнопкой «👥 Указать пассажиров».",
        )
    except Exception:
        pass


async def daily_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает ответ 'Да' / 'Нет' на ежедневную проверку.
    'Да' — ничего не меняем.
    'Нет' — сразу очищаем запись.
    """
    tg_id = update.effective_user.id
    txt = (update.message.text or "").strip().lower()

    if tg_id not in pending_confirmations:
        # это не ответ на daily-check
        return

    # убрать таймер
    job = pending_confirmations[tg_id]["job"]
    try:
        job.schedule_removal()
    except Exception:
        pass
    pending_confirmations.pop(tg_id, None)

    if txt == "да":
        await update.message.reply_text("✅ Ок, ничего не меняю.")
        await show_menu(update, context)
        return

    if txt == "нет":
        # сразу очистка
        fake_job = type("J", (), {})()
        fake_job.data = {"tg_id": tg_id}
        fake_context = type("C", (), {})()
        fake_context.job = fake_job
        fake_context.bot = context.bot
        await daily_timeout_clear(fake_context)
        await show_menu(update, context)
        return


# =========================
# BASIC COMMANDS
# =========================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_menu(update, context)

async def shutdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USERS:
        await update.message.reply_text("Нет доступа.")
        await show_menu(update, context)
        return
    await update.message.reply_text("Останавливаюсь ✅")
    await context.application.stop()
    await context.application.shutdown()

async def my_driver_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    driver, _ = get_driver(update.effective_user.id)
    if not driver:
        await update.message.reply_text("Вы не найдены в drivers.")
        await show_menu(update, context)
        return

    # passengers from drivers_passengers
    dp = ws(DRIVERS_PASSENGERS_SHEET).get_all_records()
    passengers = []
    for row in dp:
        if str(row.get("TGID")) == str(update.effective_user.id):
            passengers = [row.get("Passenger1",""), row.get("Passenger2",""), row.get("Passenger3",""), row.get("Passenger4","")]
            passengers = [p for p in passengers if p]
            break

    msg = f"🚗 Ваш водитель:\nName: {driver.get('Name')}\nShift: {driver.get('Shift')}\nPhone: {driver.get('Phone number')}\n\n"
    msg += "👥 Пассажиры:\n"
    msg += "\n".join([f"- {p}" for p in passengers]) if passengers else "- (нет)"

    await update.message.reply_text(msg)
    await show_menu(update, context)

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ок, отменил.")
    await show_menu(update, context)
    return ConversationHandler.END


# =========================
# MAIN
# =========================

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(60)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .build()
    )

    # commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("shutdown", shutdown_cmd))
    app.add_handler(CommandHandler("my_driver", my_driver_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    # кнопки меню
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_MY}$"), my_driver_cmd))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_CANCEL}$"), cancel_cmd))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_SHUT}$"), shutdown_cmd))

    # add_driver conversation
    add_driver_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_ADD}$"), add_driver_start)],
        states={
    ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_driver_name)],
    CONFIRM_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_phone)],
    ADD_SHIFT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_driver_shift)],
    ADD_CAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_driver_car)],
    ADD_PLATES: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_driver_plates)],
    },

        fallbacks=[MessageHandler(filters.Regex(f"^{BTN_CANCEL}$"), cancel_cmd)],
    )
    app.add_handler(add_driver_conv)

    # passengers conversation
    passengers_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_PASS}$"), passengers_start)],
        states={PASS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, passengers_input)]},
        fallbacks=[MessageHandler(filters.Regex(f"^{BTN_CANCEL}$"), cancel_cmd)],
    )
    app.add_handler(passengers_conv)

    # delete passenger conversation
    delete_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_DEL}$"), delete_start)],
        states={DEL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_input)]},
        fallbacks=[MessageHandler(filters.Regex(f"^{BTN_CANCEL}$"), cancel_cmd)],
    )
    app.add_handler(delete_conv)

    # daily answers (Да/Нет)
    app.add_handler(MessageHandler(filters.Regex(r"^(Да|да|Нет|нет)$"), daily_answer_handler))

    # daily jobs (Memphis time)
    app.job_queue.run_daily(daily_ask_driver, time=parse_time(DAY_SHIFT_TIME), data="day")
    app.job_queue.run_daily(daily_ask_driver, time=parse_time(NIGHT_SHIFT_TIME), data="night")

    print("Bot started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
