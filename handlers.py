from __future__ import annotations

from datetime import timedelta
from typing import Optional

from telegram import Update, ReplyKeyboardMarkup
from telegram.error import BadRequest
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


def _strip_markdown_v1(s: str) -> str:
    # Remove basic Telegram Markdown v1 markers to create a safe plain-text fallback.
    return (
        s.replace("`", "")
         .replace("*", "")
         .replace("_", "")
         .replace("[", "")
         .replace("]", "")
    )


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

    def kb_main(self):
        return ReplyKeyboardMarkup(
            [
                [Buttons.BECOME_DRIVER, Buttons.ADD_PASSENGERS],
                [Buttons.MY_RECORD, Buttons.STOP_BEING_DRIVER],
                [Buttons.REMOVE_PASSENGER],
                [Buttons.ADMIN_WEEKLY_TARGET],
                [Buttons.CANCEL],
            ],
            resize_keyboard=True,
        )

    def kb_yes_no(self):
        return ReplyKeyboardMarkup(
            [[Buttons.YES, Buttons.NO]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )


    async def _reply_md_safe(self, update: Update, text: str, **kwargs):
        """Send Markdown message, but never crash on BadRequest (fallback to plain text)."""
        try:
            await update.message.reply_text(text, parse_mode="Markdown", **kwargs)
        except BadRequest:
            await update.message.reply_text(_strip_markdown_v1(text), **kwargs)
    # ======================================================
    # Start / Cancel
    # ======================================================

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Привет! Я помогу вести список водителей и пассажиров.\n\n"
            "Выбери действие кнопками ниже:",
            reply_markup=self.kb_main(),
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text(
            "Ок, отменил 👍",
            reply_markup=self.kb_main(),
        )
        return ConversationHandler.END

    # ======================================================
    # Become driver
    # ======================================================

    async def become_driver_start(self, update, context):
        await update.message.reply_text(
            "Напиши *имя и фамилию* как в таблице сотрудников (employees).\n"
            "Пример: `Ivan Ivanov`",
            parse_mode="Markdown",
        )
        return ST_DRIVER_NAME

    async def become_driver_name(self, update, context):
        name = update.message.text.strip()
        emp = self.sheets.get_employee_by_name(name)
        if not emp:
            await update.message.reply_text(
                "Сотрудник не найден 😕\n"
                "Проверь написание *точно как в employees*.\n"
                "Пример: `Ivan Ivanov`",
                parse_mode="Markdown",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        # Если сотрудник уже является пассажиром — не даём стать водителем
        if (emp.rides_with or "").strip():
            await self._reply_md_safe(update,
                "Похоже, сейчас ты *пассажир* (rides_with заполнен).\n\n"
                f"Сначала тебя нужно убрать из пассажиров у водителя: *{emp.rides_with.strip()}*.\n"
                "Попроси водителя нажать кнопку «🧑‍🤝‍🧑 Удалить пассажира» и удалить тебя из списка.\n\n"
                "После этого ты сможешь стать водителем 🚗",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        # Дополнительная защита: даже если rides_with не заполнен,
        # проверяем фактическое присутствие в drivers_passengers.
        hit = self.sheets.find_driver_for_passenger(emp.name)
        if hit:
            driver_tg, driver_name = hit
            driver_label = driver_name or str(driver_tg)
            await self._reply_md_safe(update,
                "Похоже, сейчас ты *пассажир* в списке водителя.\n\n"
                f"Водитель: *{driver_label}*\n"
                "Сначала попроси водителя удалить тебя кнопкой «🧑‍🤝‍🧑 Удалить пассажира».\n\n"
                "После этого ты сможешь стать водителем 🚗",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        context.user_data["driver_name"] = emp.name
        await update.message.reply_text(
            "Марка/модель машины?\nПример: `Kia Rio`",
            parse_mode="Markdown",
        )
        return ST_DRIVER_CAR

    async def become_driver_car(self, update, context):
        context.user_data["driver_car"] = update.message.text.strip()
        await update.message.reply_text(
            "Licence Plates?\nПример: `ABC123`",
            parse_mode="Markdown",
        )
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
        await update.message.reply_text(
            "✅ Запись водителя сохранена.\n"
            "Теперь можешь добавить пассажиров кнопкой «👥 Добавить пассажиров».",
            reply_markup=self.kb_main(),
        )
        return ConversationHandler.END

    # ======================================================
    # My record
    # ======================================================

    async def my_record(self, update, context):
        tg_id = update.effective_user.id
        driver = self.sheets.get_driver(tg_id)

        if not driver:
            await update.message.reply_text(
                "У тебя нет записи водителя.",
                reply_markup=self.kb_main(),
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

        await update.message.reply_text(txt, reply_markup=self.kb_main())

    # ======================================================
    # Stop being driver
    # ======================================================

    async def stop_being_driver_start(self, update, context):
        tg_id = update.effective_user.id
        if not self.sheets.get_driver(tg_id):
            await update.message.reply_text(
                "У тебя нет записи водителя.",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        await update.message.reply_text(
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

            # Очищаем employees.rides_with у пассажиров и у самого водителя
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

            # Удаляем строки из drivers и drivers_passengers
            self.sheets.delete_driver(tg_id)
            self.sheets.delete_driver_passengers(tg_id)

            await self.log_admin(
                context,
                "Driver stopped being driver",
                f"tg_id={tg_id}\npassengers={len(passenger_names)}\ncleared_rides_with_rows={cleared}",
                update,
            )
            await update.message.reply_text(
                "✅ Готово! Ты больше не водитель.\n"
                "Теперь тебя можно добавить пассажиром 😉",
                reply_markup=self.kb_main(),
            )
        else:
            await update.message.reply_text(
                "Ок, ничего не меняю.",
                reply_markup=self.kb_main(),
            )

        return ConversationHandler.END

    # ======================================================
    # Add passengers
    # ======================================================

    async def add_passengers_start(self, update, context):
        tg_id = update.effective_user.id
        if not self.sheets.get_driver(tg_id):
            await update.message.reply_text(
                "Сначала нужно стать водителем.\n"
                "Нажми «🚗 Стать водителем» и заполни данные.",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        await update.message.reply_text(
            "Введи пассажиров (каждого с новой строки), максимум *4*.\n\n"
            "Пример:\n"
            "`Ivan Ivanov`\n"
            "`Maria Ivanova`",
            parse_mode="Markdown",
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
            await update.message.reply_text(
                "\n\n".join(errors),
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        driver = self.sheets.get_driver(tg_id)
        dp = DriverPassengers(
            driver_name=driver.name,
            driver_tgid=tg_id,
            passengers=[e.name for e in valid],
        )
        self.sheets.upsert_driver_passengers(dp)

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

        await update.message.reply_text(
            "\n\n".join(parts),
            reply_markup=self.kb_main(),
        )
        return ConversationHandler.END

    # ======================================================
    # Remove passenger
    # ======================================================

    async def remove_passenger_start(self, update, context):
        tg_id = update.effective_user.id
        dp = self.sheets.get_driver_passengers(tg_id)

        if not dp or not dp.passengers:
            await update.message.reply_text(
                "У тебя нет пассажиров.",
                reply_markup=self.kb_main(),
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
        await update.message.reply_text(
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
            await update.message.reply_text(
                "Нет данных о пассажирах.",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        # Ищем совпадение без учёта регистра
        match = next(
            (p for p in dp.passengers if p.casefold() == chosen.casefold()),
            None,
        )

        if not match:
            await update.message.reply_text(
                "Пассажир не найден — попробуй снова.",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        dp.passengers.remove(match)
        self.sheets.upsert_driver_passengers(dp)

        await self.log_admin(
            context, "Passenger removed",
            f"Driver tg_id={tg_id}, removed={match}", update,
        )

        # Показываем обновлённый список или сообщение если пусто
        if dp.passengers:
            remaining = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(dp.passengers))
            await update.message.reply_text(
                f"Пассажир «{match}» удалён.\n\nОставшиеся:\n{remaining}",
                reply_markup=self.kb_main(),
            )
        else:
            await update.message.reply_text(
                f"Пассажир «{match}» удалён. Список пассажиров пуст.",
                reply_markup=self.kb_main(),
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

        context.job_queue.run_once(
            self._weekly_timeout,
            when=timedelta(minutes=self.config.CONFIRMATION_TIMEOUT_MINUTES),
            data={"tg_id": tg_id, "shift": shift},
        )

    async def _weekly_timeout(self, context):
        data = context.job.data
        tg_id = data["tg_id"]
        state = get_state_manager(self.config.STATE_FILE)

        if not state.is_pending(tg_id):
            return

        dp = self.sheets.get_driver_passengers(tg_id)
        if dp:
            dp.passengers = []
            self.sheets.upsert_driver_passengers(dp)

        state.remove_pending(tg_id)
        await self.log_admin(
            context, "Weekly timeout — список очищен", f"tg_id={tg_id}",
        )

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
            await update.message.reply_text(
                "Ок, список оставлен.",
                reply_markup=self.kb_main(),
            )
        else:
            dp = self.sheets.get_driver_passengers(tg_id)
            if dp:
                dp.passengers = []
                self.sheets.upsert_driver_passengers(dp)
            state.remove_pending(tg_id)
            await self.log_admin(
                context, "Weekly ответ", "❌ Нет (очистка)", update,
            )
            await update.message.reply_text(
                "Список очищен.",
                reply_markup=self.kb_main(),
            )

    # ======================================================
    # Admin weekly
    # ======================================================

    async def admin_weekly_start(self, update, context):
        # доступ только админам
        uid = update.effective_user.id
        if uid not in (self.config.ADMIN_USER_IDS or []):
            await update.message.reply_text(
                "⛔ Эта команда доступна только администраторам.",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        await update.message.reply_text(
            "Админка: точечная weekly-проверка.\n\nВыбери режим:",
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
            await update.message.reply_text(
                "Введи Telegram ID водителя (число).\n"
                "Пример: `123456789`",
                parse_mode="Markdown",
            )
            return ST_ADMIN_TGID

        if txt == Buttons.ADMIN_MODE_SHIFT:
            await update.message.reply_text(
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
            await update.message.reply_text(
                "TGID должен быть числом.\nПример: `123456789`",
                parse_mode="Markdown",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        tg_id = int(raw)

        if not self.sheets.get_driver(tg_id):
            await update.message.reply_text(
                f"Водитель с TGID {tg_id} не найден.",
                reply_markup=self.kb_main(),
            )
            return ConversationHandler.END

        shift = self.sheets.get_shift_for_tgid(tg_id)
        await self._send_weekly(context, tg_id, shift.value)

        await self.log_admin(
            context, "Admin weekly TGID",
            f"{tg_id} shift={shift.value}", update,
        )
        await update.message.reply_text(
            f"Weekly отправлен водителю {tg_id}.",
            reply_markup=self.kb_main(),
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
        await update.message.reply_text(
            f"Weekly отправлен {len(tgids)} водителям смены {shift.value}.",
            reply_markup=self.kb_main(),
        )
        return ConversationHandler.END