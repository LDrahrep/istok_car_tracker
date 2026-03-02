from __future__ import annotations

import time

from typing import Optional

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import Buttons
from models import Driver, DriverPassengers, ShiftType
from persistence import get_state_manager


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
) = range(20, 29)


class BotHandlers:
    def __init__(self, config, sheets):
        self.config = config
        self.sheets = sheets

    # ======================================================
    # Utility
    # ======================================================

    async def log_admin(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        title: str,
        details: str = "",
        update: Optional[Update] = None,
    ):
        if not self.config.ADMIN_CHAT_ID:
            return

        uid = update.effective_user.id if update else None
        uname = update.effective_user.username if update else None

        msg = f"🧾 {title}"
        meta = []
        if uid:
            meta.append(f"uid={uid}")
        if uname:
            meta.append(f"@{uname}")
        if meta:
            msg += "\n(" + " | ".join(meta) + ")"
        if details:
            msg += "\n" + details

        try:
            await context.bot.send_message(
                chat_id=self.config.ADMIN_CHAT_ID,
                text=msg,
            )
        except Exception:
            pass

    def kb_main(self, user_id: int | None = None):
        keyboard = [
            [Buttons.BECOME_DRIVER, Buttons.ADD_PASSENGERS],
            [Buttons.MY_RECORD, Buttons.STOP_BEING_DRIVER],
            [Buttons.REMOVE_PASSENGER],
        ]

        # Админскую кнопку показываем только администраторам
        if user_id is not None and user_id in self.config.ADMIN_USER_IDS:
            keyboard.append([Buttons.ADMIN_WEEKLY_TARGET])

        keyboard.append([Buttons.CANCEL])

        return ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
        )

    def kb_yes_no(self):
        return ReplyKeyboardMarkup(
            [[Buttons.YES, Buttons.NO]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )

    async def _reply(self, update: Update, text: str, **kwargs):
        """Safe wrapper around reply_text.

        We intentionally do NOT use Markdown/HTML parse modes because user-provided
        data (names, plates, usernames) may contain characters that break entity
        parsing in Telegram and crash the bot.
        """
        return await update.message.reply_text(text, **kwargs)


    def _throttle(self, context: ContextTypes.DEFAULT_TYPE, key: str, seconds: int) -> bool:
        """Simple in-memory throttle (stored in application.bot_data).

        Returns True if we are allowed to emit a log now, otherwise False.
        """
        now = time.time()
        store = context.application.bot_data.setdefault("_throttle", {})
        last = store.get(key, 0.0)
        if now - last < seconds:
            return False
        store[key] = now
        return True

    async def unknown(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Fallback handler for messages not matched by any other handler.

        We log this to the admin chat (throttled) and show the user a friendly hint.
        """
        u = update.effective_user
        if not u:
            return

        txt = ""
        if update.message and update.message.text:
            txt = update.message.text

        # Антифлуд: не чаще 1 unknown/20сек на пользователя
        if not self._throttle(context, f"unknown:{u.id}", 20):
            return

        await self.log_admin(
            context,
            "Unknown message",
            f"text={txt!r}"[:1500],
            update,
        )

        if update.message:
            await self._reply(
                update,
                "Я не понял сообщение 🤔\nИспользуй кнопки на клавиатуре или команду /start",
                reply_markup=self.kb_main(u.id),
            )

    def _is_real_passenger_emp(self, emp) -> bool:
        """Считать сотрудника пассажиром только если rides_with заполнен И не равен его собственному имени.
        Это позволяет использовать rides_with = своё имя как 'защиту' для водителей.
        """
        try:
            rides = (emp.rides_with or "").strip()
            if not rides:
                return False
            return rides.casefold().strip() != (emp.name or "").casefold().strip()
        except Exception:
            return False

# ======================================================
    # Start / Cancel
    # ======================================================

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._reply(
            update,
            "Привет! Я помогу вести список водителей и пассажиров.\n\n"
            "Выбери действие кнопками ниже:",
            reply_markup=self.kb_main(update.effective_user.id),
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await self._reply(
            update,
            "Ок, отменил 👍",
            reply_markup=self.kb_main(update.effective_user.id),
        )
        return ConversationHandler.END

    # ======================================================
    # Become driver
    # ======================================================

    async def become_driver_start(self, update, context):
        await self._reply(
            update,
            "Напиши имя и фамилию.\n"
            "Пример: Ivan Ivanov",
        )
        return ST_DRIVER_NAME

    async def become_driver_name(self, update, context):
        tg_id = update.effective_user.id
        name = update.message.text.strip()
        emp = self.sheets.get_employee_by_name(name)
        if not emp:
            await self._reply(
                update,
                "Сотрудник не найден 😕\n"
                "Проверь написание имени и фамилии.\n"
                "Пример: Ivan Ivanov",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Если сотрудник уже является пассажиром — не даём стать водителем
        if self._is_real_passenger_emp(emp):
            await self._reply(
                update,
                "Похоже, сейчас ты записан как пассажир.\n\n"
                f"Сначала тебя нужно убрать из списка водителя: {emp.rides_with.strip()}.\n"
                "Попроси водителя нажать кнопку «🧑‍🤝‍🧑 Удалить пассажира» и удалить тебя из списка.\n\n"
                "После этого ты сможешь стать водителем 🚗",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Дополнительная защита: даже если rides_with не заполнен,
        # проверяем фактическое присутствие в drivers_passengers.
        hit = self.sheets.find_driver_for_passenger(emp.name)
        if hit:
            driver_tg, driver_name = hit
            # Если найден "водитель" == текущий пользователь (сам себе пассажир) — игнорируем.
            if int(driver_tg) == int(tg_id) or (driver_name and driver_name.casefold().strip() == (emp.name or "").casefold().strip()):
                hit = None
        if hit:
            driver_tg, driver_name = hit
            driver_label = driver_name or str(driver_tg)
            await self._reply(
                update,
                "Похоже, сейчас ты пассажир в списке водителя.\n\n"
                f"Водитель: {driver_label}\n"
                "Сначала попроси водителя удалить тебя кнопкой «🧑‍🤝‍🧑 Удалить пассажира».\n\n"
                "После этого ты сможешь стать водителем 🚗",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        context.user_data["driver_name"] = emp.name
        await self._reply(update, "Марка/модель машины?\nПример: Kia Rio")
        return ST_DRIVER_CAR

    async def become_driver_car(self, update, context):
        context.user_data["driver_car"] = update.message.text.strip()
        await self._reply(update, "Licence Plates?\nПример: ABC123")
        return ST_DRIVER_PLATES

    async def become_driver_plates(self, update, context):
        tg_id = update.effective_user.id
        driver = Driver(
            name=context.user_data["driver_name"],
            tg_id=tg_id,
            car=context.user_data["driver_car"],
            plates=update.message.text.strip(),
        )
        self.sheets.upsert_driver(driver)
        await self.log_admin(
            context, "Driver created/updated",
            f"{driver.name} ({tg_id})", update,
        )
        await self._reply(
            update,
            "✅ Запись водителя сохранена.\n"
            "Теперь можешь добавить пассажиров кнопкой «👥 Добавить пассажиров».",
            reply_markup=self.kb_main(update.effective_user.id),
        )
        return ConversationHandler.END

    # ======================================================
    # My record
    # ======================================================

    async def my_record(self, update, context):
        tg_id = update.effective_user.id
        driver = self.sheets.get_driver(tg_id)

        if not driver:
            await self._reply(
                update,
                "У тебя нет записи водителя.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return

        dp = self.sheets.get_driver_passengers(tg_id)
        passengers = dp.passengers if dp else []

        txt = (
            f"📋 Твоя запись:\n\n"
            f"👤 Имя: {driver.name}\n"
            f"🚗 Машина: {driver.car}\n"
            f"🔖 Licence Plates: {driver.plates}\n\n"
        )
        if passengers:
            txt += "👥 Пассажиры:\n" + "\n".join(
                f"  {i+1}. {p}" for i, p in enumerate(passengers)
            )
        else:
            txt += "👥 Пассажиры: нет"

        await self._reply(update, txt, reply_markup=self.kb_main(update.effective_user.id))

    # ======================================================
    # Stop being driver
    # ======================================================

    async def stop_being_driver_start(self, update, context):
        tg_id = update.effective_user.id
        if not self.sheets.get_driver(tg_id):
            await self._reply(
                update,
                "У тебя нет записи водителя.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        await self._reply(
            update,
            "Ты точно хочешь перестать быть водителем?\n\n"
            "Я удалю твою запись водителя и отвяжу пассажиров.",
            reply_markup=self.kb_yes_no(),
        )
        return ST_STOP_CONFIRM

    async def stop_being_driver_confirm(self, update, context):
        tg_id = update.effective_user.id

        if update.message.text == Buttons.YES:
            # Собираем текущих пассажиров (по именам) до удаления
            dp = self.sheets.get_driver_passengers(tg_id)
            passenger_names = set(dp.passengers) if dp else set()

            # ВАЖНО: сначала удаляем из drivers_passengers (source of truth),
            # чтобы Apps Script syncEmployeesAll не вернул данные обратно.
            self.sheets.delete_driver_passengers(tg_id)
            self.sheets.delete_driver(tg_id)

            # Теперь очищаем employees (Rides with + telegramID)
            cleared = 0
            try:
                cleared = self.sheets.clear_rides_with(
                    tg_ids={tg_id},
                    names=passenger_names,
                )
            except Exception as e:
                await self.log_admin(
                    context,
                    "Exception while clearing rides_with",
                    str(e)[-1500:],
                    update,
                )

            await self.log_admin(
                context,
                "Driver stopped being driver",
                f"tg_id={tg_id}\npassengers={len(passenger_names)}\ncleared_rides_with_rows={cleared}",
                update,
            )
            await update.message.reply_text(
                "✅ Готово! Ты больше не водитель.\n"
                "Теперь тебя можно добавить пассажиром 😉",
                reply_markup=self.kb_main(update.effective_user.id),
            )
        else:
            await self._reply(
                update,
                "Ок, ничего не меняю.",
                reply_markup=self.kb_main(update.effective_user.id),
            )

        return ConversationHandler.END

    # ======================================================
    # Add passengers
    # ======================================================

    async def add_passengers_start(self, update, context):
        tg_id = update.effective_user.id
        if not self.sheets.get_driver(tg_id):
            await self._reply(
                update,
                "Сначала нужно стать водителем.\n"
                "Нажми «🚗 Стать водителем» и заполни данные.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        await self._reply(
            update,
            "Введи пассажиров (каждого с новой строки), максимум 4.\n\n"
            "Пример:\n"
            "Ivan Ivanov\n"
            "Maria Ivanova",
        )
        return ST_ADD_PASSENGERS

    async def add_passengers_input(self, update, context):
        tg_id = update.effective_user.id
        names = [
            x.strip()
            for x in update.message.text.splitlines()
            if x.strip()
        ]

        valid, errors, warnings = self.sheets.validate_passengers(tg_id, names)

        if errors:
            await self._reply(
                update,
                "\n\n".join(errors),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        driver = self.sheets.get_driver(tg_id)
        dp = DriverPassengers(
            driver_name=driver.name,
            driver_tgid=tg_id,
            passengers=[e.name for e in valid],
        )
        self.sheets.upsert_driver_passengers(dp)

        # Записываем в employees: Rides with и telegramID для пассажиров
        self.sheets.assign_passengers_to_driver(
            driver_tgid=tg_id,
            driver_name=driver.name,
            passenger_names=[e.name for e in valid],
        )

        await self.log_admin(
            context, "Passengers updated",
            f"Driver {driver.name}\n" + "\n".join([e.name for e in valid]),
            update,
        )
        # UX: одно итоговое сообщение — кого добавили и кого пропустили
        added_names = [e.name for e in valid]
        parts = ["✅ Пассажиры сохранены."]

        if added_names:
            parts.append("👥 Добавлены:\n" + "\n".join([f"• {n}" for n in added_names]))
        else:
            parts.append("👥 Никого не добавил (все пункты были пропущены).")

        if warnings:
            # warnings уже дружелюбные и с подсказками; оформим списком
            parts.append(
                "⛔ Пропущены:\n" + "\n".join([f"• {w}" for w in warnings])
            )

        await self._reply(
            update,
            "\n\n".join(parts),
            reply_markup=self.kb_main(update.effective_user.id),
        )
        return ConversationHandler.END

    # ======================================================
    # Remove passenger
    # ======================================================

    async def remove_passenger_start(self, update, context):
        tg_id = update.effective_user.id
        dp = self.sheets.get_driver_passengers(tg_id)

        if not dp or not dp.passengers:
            await self._reply(
                update,
                "У тебя нет пассажиров.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Сохраняем список чтобы потом сверить выбор
        context.user_data["passengers_to_remove_from"] = dp.passengers[:]

        # Каждый пассажир — отдельная кнопка, плюс «Назад»
        kb = ReplyKeyboardMarkup(
            [[p] for p in dp.passengers] + [[Buttons.CANCEL]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await self._reply(
            update,
            "Выбери пассажира для удаления (кнопкой ниже):",
            reply_markup=kb,
        )
        return ST_REMOVE_PASSENGER

    async def remove_passenger_input(self, update, context):
        tg_id = update.effective_user.id
        chosen = update.message.text.strip()

        # Получаем актуальный список из sheets (не из кэша user_data)
        dp = self.sheets.get_driver_passengers(tg_id)
        if not dp:
            await self._reply(
                update,
                "Нет данных о пассажирах.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Ищем совпадение без учёта регистра
        match = next(
            (p for p in dp.passengers if p.casefold() == chosen.casefold()),
            None,
        )

        if not match:
            await self._reply(
                update,
                "Пассажир не найден — попробуй снова.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        dp.passengers.remove(match)
        self.sheets.upsert_driver_passengers(dp)

        # Очищаем Rides with и telegramID для удалённого пассажира в employees
        self.sheets.clear_rides_with(names={match})

        await self.log_admin(
            context, "Passenger removed",
            f"Driver tg_id={tg_id}, removed={match}", update,
        )

        # Показываем обновлённый список или сообщение если пусто
        if dp.passengers:
            remaining = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(dp.passengers))
            await self._reply(
                update,
                f"Пассажир «{match}» удалён.\n\nОставшиеся:\n{remaining}",
                reply_markup=self.kb_main(update.effective_user.id),
            )
        else:
            await self._reply(
                update,
                f"Пассажир «{match}» удалён. Список пассажиров пуст.",
                reply_markup=self.kb_main(update.effective_user.id),
            )

        context.user_data.pop("passengers_to_remove_from", None)
        return ConversationHandler.END

    # ======================================================
    # Weekly
    # ======================================================

    async def _send_weekly(self, context, tg_id, shift):
        dp = self.sheets.get_driver_passengers(tg_id)
        passengers = dp.passengers if dp else []

        txt = "📅 Еженедельная проверка списка пассажиров\n\n"
        txt += "Текущие пассажиры:\n"
        txt += "\n".join(passengers) if passengers else "Нет пассажиров"
        txt += "\n\nВсё актуально?"

        await self.log_admin(
            context,
            "Weekly send",
            f"tg_id={tg_id} shift={shift} passengers={len(passengers)}",
        )

        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=txt,
                reply_markup=self.kb_yes_no(),
            )
        except Exception as e:
            await self.log_admin(
                context,
                "Weekly send failed",
                f"tg_id={tg_id} err={str(e)[-1500:]}",
            )
            return

        state = get_state_manager(self.config.STATE_FILE)
        state.add_pending(tg_id, shift)

    async def weekly_answer(self, update, context):
        tg_id = update.effective_user.id
        state = get_state_manager(self.config.STATE_FILE)

        if not state.is_pending(tg_id):
            return

        if update.message.text == Buttons.YES:
            state.remove_pending(tg_id)
            await self.log_admin(
                context, "Weekly ответ", "✅ Да", update,
            )
            await self._reply(
                update,
                "Ок, список оставлен.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
        else:
            dp = self.sheets.get_driver_passengers(tg_id)
            if dp:
                old_passengers = dp.passengers[:]
                dp.passengers = []
                self.sheets.upsert_driver_passengers(dp)
                # Очищаем Rides with и telegramID пассажиров в employees
                if old_passengers:
                    self.sheets.clear_rides_with(names=set(old_passengers))
            state.remove_pending(tg_id)
            await self.log_admin(
                context, "Weekly ответ", "❌ Нет (очистка)", update,
            )
            await self._reply(
                update,
                "Список очищен.",
                reply_markup=self.kb_main(update.effective_user.id),
            )

    # ======================================================
    # Admin weekly
    # ======================================================

    async def admin_weekly_start(self, update, context):
        # доступ только админам
        uid = update.effective_user.id
        if uid not in (self.config.ADMIN_USER_IDS or []):
            if self._throttle(context, f"admin_denied:{uid}", 60):
                await self.log_admin(context, "Admin access denied", "", update)
            await self._reply(
                update,
                "⛔ Эта команда доступна только администраторам.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        await self._reply(
            update,
            "Точечная проверка пассажиров.\n\nВыбери режим:",
            reply_markup=ReplyKeyboardMarkup(
                [
                    [Buttons.ADMIN_MODE_TGID],
                    [Buttons.ADMIN_MODE_SHIFT],
                    [Buttons.CANCEL],
                ],
                resize_keyboard=True,
            ),
        )
        return ST_ADMIN_MODE

    async def admin_mode(self, update, context):
        txt = update.message.text

        if txt == Buttons.ADMIN_MODE_TGID:
            await self._reply(update, "Введи Telegram ID водителя (число).\nПример: 123456789")
            return ST_ADMIN_TGID

        if txt == Buttons.ADMIN_MODE_SHIFT:
            await self._reply(
                update,
                "Выбери смену:",
                reply_markup=ReplyKeyboardMarkup(
                    [[Buttons.SHIFT_DAY], [Buttons.SHIFT_NIGHT]],
                    resize_keyboard=True,
                ),
            )
            return ST_ADMIN_SHIFT

        return ConversationHandler.END

    async def admin_tgid(self, update, context):
        raw = update.message.text.strip()

        if not raw.isdigit():
            await self._reply(
                update,
                "Telegram ID должен быть числом.\nПример: 123456789",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        tg_id = int(raw)

        if not self.sheets.get_driver(tg_id):
            await self._reply(
                update,
                f"Водитель с Telegram ID {tg_id} не найден.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        shift = self.sheets.get_shift_for_tgid(tg_id)
        await self._send_weekly(context, tg_id, shift.value)

        await self.log_admin(
            context, "Admin weekly TGID",
            f"{tg_id} shift={shift.value}", update,
        )
        await self._reply(
            update,
            f"Проверка отправлена водителю (ID: {tg_id}).",
            reply_markup=self.kb_main(update.effective_user.id),
        )
        return ConversationHandler.END

    async def admin_shift(self, update, context):
        txt = update.message.text
        shift = (
            ShiftType.DAY if txt == Buttons.SHIFT_DAY else ShiftType.NIGHT
        )

        values = self.sheets._values(self.config.DRIVERS_PASSENGERS_SHEET)
        headers = values[0]
        col = self.sheets._col_map(headers)
        tg_col = col.get("telegramID")

        if tg_col is None:
            await self.log_admin(context, "Admin weekly by shift failed", "telegramID column not found")
            await self._reply(
                update,
                "Произошла ошибка. Обратись к администратору.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        tgids = []
        for row in values[1:]:
            if tg_col < len(row):
                raw = row[tg_col].strip()
                if raw.isdigit():
                    tid = int(raw)
                    if self.sheets.get_shift_for_tgid(tid) == shift:
                        tgids.append(tid)

        for tid in tgids:
            await self._send_weekly(context, tid, shift.value)

        await self.log_admin(
            context, "Admin weekly by shift",
            f"{shift.value} count={len(tgids)}", update,
        )
        await self._reply(
            update,
            f"Проверка отправлена {len(tgids)} водителям ({shift.to_display()}).",
            reply_markup=self.kb_main(update.effective_user.id),
        )
        return ConversationHandler.END

    # ======================================================
    # Broadcast keyboard (admin only)
    # ======================================================

    async def broadcast_keyboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отправить всем ВОДИТЕЛЯМ сообщение с обновлённой клавиатурой.

        Используем таблицу drivers — там telegramID это настоящий ID водителя.
        В employees.telegramID хранится ID водителя (не сотрудника), поэтому
        employees не подходит для рассылки.
        """
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        # Берём уникальные telegramID из таблицы drivers
        driver_tg_ids = self.sheets.get_all_driver_tgids()
        sent = 0
        failed = 0

        for tg_id in driver_tg_ids:
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text="🔄 Бот обновлён! Клавиатура обновлена.\n"
                         "Используй кнопки ниже:",
                    reply_markup=self.kb_main(tg_id),
                )
                sent += 1
            except Exception:
                failed += 1

        await self._reply(
            update,
            f"✅ Клавиатура отправлена: {sent} водителям.\n"
            f"{'❌ Не удалось: ' + str(failed) if failed else ''}",
            reply_markup=self.kb_main(uid),
        )