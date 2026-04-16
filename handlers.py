from __future__ import annotations

import asyncio
import difflib
import logging
import time

from typing import Optional

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import Buttons
from i18n import t, button, set_user_lang, is_button
from models import Driver, DriverPassengers, ShiftType, normalize_text
from persistence import get_state_manager

logger = logging.getLogger(__name__)


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
            [button("btn.become_driver", user_id), button("btn.add_passengers", user_id)],
            [button("btn.my_record", user_id), button("btn.stop_being_driver", user_id)],
            [button("btn.remove_passenger", user_id)],
        ]

        # Админскую кнопку показываем только администраторам
        if user_id is not None and user_id in self.config.ADMIN_USER_IDS:
            keyboard.append([button("btn.admin_weekly_target", user_id)])

        keyboard.append([button("btn.cancel", user_id)])

        return ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
        )

    def kb_yes_no(self, user_id: int | None = None):
        return ReplyKeyboardMarkup(
            [[button("btn.yes", user_id), button("btn.no", user_id)]],
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
            # Попробуем предложить похожие имена
            all_emp = self.sheets.get_all_employees()
            all_names = [e.name for e in all_emp if e.name]
            suggestions = difflib.get_close_matches(
                name, all_names, n=3, cutoff=0.6,
            )
            logger.info(
                "become_driver_name: NOT FOUND %r, all_names=%d, suggestions=%r",
                name, len(all_names), suggestions,
            )
            msg = "Сотрудник не найден 😕\n"
            if suggestions:
                msg += "Возможно, ты имел в виду:\n"
                msg += "\n".join(f"• {s}" for s in suggestions)
                msg += "\n\nПопробуй ещё раз."
            else:
                msg += "Проверь написание имени и фамилии.\nПример: Ivan Ivanov"
            await self._reply(
                update,
                msg,
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

        # Защита: проверяем, не зарегистрирован ли уже другой водитель с этим именем
        if self.sheets.is_name_taken_by_other_driver(emp.name, tg_id):
            await self._reply(
                update,
                "⛔ Другой водитель уже зарегистрирован с этим именем.\n"
                "Если это ошибка — обратись к администратору.",
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
        try:
            self.sheets.upsert_driver(driver)
        except Exception as e:
            await self.log_admin(
                context, "Sheet write error (upsert driver)",
                str(e)[-1500:], update,
            )
            await self._reply(
                update,
                "❌ Ошибка при сохранении. Попробуй ещё раз.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

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

        # Проверяем консистентность смен перед показом
        shift_removed = self.sheets.enforce_shift_consistency(tg_id)

        dp = self.sheets.get_driver_passengers(tg_id)
        passengers = dp.passengers if dp else []

        txt = ""
        if shift_removed:
            txt += (
                "⚠️ Пассажиры удалены из-за смены Shift:\n"
                + "\n".join(f"• {n}" for n in shift_removed)
                + "\n\n"
            )
            await self.log_admin(
                context, "Shift consistency cleanup (my_record)",
                f"driver_tgid={tg_id} removed={shift_removed}", update,
            )

        txt += (
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

        if is_button(update.message.text, "btn.yes"):
            # Сохраняем бэкапы ДО удаления для возможного отката
            dp_backup = self.sheets.get_driver_passengers(tg_id)
            driver_backup = self.sheets.get_driver(tg_id)
            passenger_names = set(dp_backup.passengers) if dp_backup else set()
            # Добавляем имя водителя (он тоже записан к себе в employees)
            driver_name = dp_backup.driver_name if dp_backup else (driver_backup.name if driver_backup else "")
            all_names = passenger_names | ({driver_name} if driver_name else set())

            try:
                # ВАЖНО: сначала удаляем из drivers_passengers (source of truth),
                # чтобы Apps Script syncEmployeesAll не вернул данные обратно.
                self.sheets.delete_driver_passengers(tg_id)
                self.sheets.delete_driver(tg_id)
                # Очищаем employees (Rides with + telegramID) по именам
                self.sheets.clear_rides_with(names=all_names)
            except Exception as e:
                # Откат: восстанавливаем удалённые записи
                try:
                    if dp_backup:
                        self.sheets.upsert_driver_passengers(dp_backup)
                    if driver_backup:
                        self.sheets.upsert_driver(driver_backup)
                except Exception:
                    pass
                await self.log_admin(
                    context,
                    "Sheet write error (stop being driver)",
                    str(e)[-1500:],
                    update,
                )
                await self._reply(
                    update,
                    "❌ Ошибка при удалении. Попробуй ещё раз.",
                    reply_markup=self.kb_main(update.effective_user.id),
                )
                return ConversationHandler.END

            await self.log_admin(
                context,
                "Driver stopped being driver",
                f"tg_id={tg_id}\npassengers={len(passenger_names)}",
                update,
            )
            await self._reply(
                update,
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

        # Проверяем консистентность смен перед добавлением
        shift_removed = self.sheets.enforce_shift_consistency(tg_id)
        prefix = ""
        if shift_removed:
            prefix = (
                "⚠️ Пассажиры удалены из-за смены Shift:\n"
                + "\n".join(f"• {n}" for n in shift_removed)
                + "\n\n"
            )
            await self.log_admin(
                context, "Shift consistency cleanup (add_passengers)",
                f"driver_tgid={tg_id} removed={shift_removed}", update,
            )

        await self._reply(
            update,
            prefix
            + "Введи пассажиров (каждого с новой строки), максимум 4.\n\n"
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

        # Нет новых валидных пассажиров — НЕ трогаем существующих
        if not valid:
            parts = ["ℹ️ Никого не удалось добавить."]
            if warnings:
                parts.append("\n".join(f"• {w}" for w in warnings))
            await self._reply(
                update,
                "\n\n".join(parts),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        driver = self.sheets.get_driver(tg_id)

        # MERGE: сохраняем существующих пассажиров + добавляем новых
        existing_dp = self.sheets.get_driver_passengers(tg_id)
        existing_passengers = existing_dp.passengers if existing_dp else []

        new_names = [e.name for e in valid]
        merged = list(existing_passengers)
        existing_norm = {normalize_text(p) for p in merged}
        for name in new_names:
            if normalize_text(name) not in existing_norm:
                merged.append(name)

        if len(merged) > 4:
            overflow = [n for n in merged[4:] if n in new_names]
            merged = merged[:4]
            for name in overflow:
                warnings.append(f"{name}: не помещается (максимум 4 пассажира).")

        dp = DriverPassengers(
            driver_name=driver.name,
            driver_tgid=tg_id,
            passengers=merged,
        )

        # Бэкап для отката при частичном сбое
        old_dp = self.sheets.get_driver_passengers(tg_id)

        try:
            self.sheets.upsert_driver_passengers(dp)
            self.sheets.assign_passengers_to_driver(
                driver_tgid=tg_id,
                driver_name=driver.name,
                passenger_names=merged,
            )
        except Exception as e:
            # Откат drivers_passengers к предыдущему состоянию
            try:
                if old_dp:
                    self.sheets.upsert_driver_passengers(old_dp)
                else:
                    self.sheets.delete_driver_passengers(tg_id)
            except Exception:
                pass
            await self.log_admin(
                context, "Sheet write error (add passengers)",
                str(e)[-1500:], update,
            )
            await self._reply(
                update,
                "❌ Произошла ошибка при сохранении. Попробуй ещё раз.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        await self.log_admin(
            context, "Passengers updated",
            f"Driver {driver.name}\nAll: {', '.join(merged)}\nNew: {', '.join(new_names)}",
            update,
        )
        parts = ["✅ Пассажиры сохранены."]
        parts.append("👥 Добавлены:\n" + "\n".join(f"• {n}" for n in new_names))

        if warnings:
            parts.append(
                "⛔ Пропущены:\n" + "\n".join(f"• {w}" for w in warnings)
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

        # Проверяем консистентность смен перед показом списка
        shift_removed = self.sheets.enforce_shift_consistency(tg_id)
        if shift_removed:
            await self.log_admin(
                context, "Shift consistency cleanup (remove_passenger)",
                f"driver_tgid={tg_id} removed={shift_removed}", update,
            )
            await self._reply(
                update,
                "⚠️ Пассажиры удалены из-за смены Shift:\n"
                + "\n".join(f"• {n}" for n in shift_removed),
            )

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
            [[p] for p in dp.passengers] + [[button("btn.cancel", update.effective_user.id)]],
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

        try:
            self.sheets.upsert_driver_passengers(dp)
            self.sheets.clear_rides_with(names={match})
        except Exception as e:
            # Откат: восстанавливаем пассажира в списке
            try:
                dp.passengers.append(match)
                self.sheets.upsert_driver_passengers(dp)
            except Exception:
                pass
            await self.log_admin(
                context, "Sheet write error (remove passenger)",
                str(e)[-1500:], update,
            )
            await self._reply(
                update,
                "❌ Ошибка при удалении. Попробуй ещё раз.",
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

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

        if is_button(update.message.text, "btn.yes"):
            state.remove_pending(tg_id)
            await self.log_admin(
                context, "Weekly ответ", "✅ Да", update,
            )
            await self._reply(
                update,
                t("weekly.yes_answer", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
        else:
            try:
                dp = self.sheets.get_driver_passengers(tg_id)
                if dp:
                    old_passengers = dp.passengers[:]
                    dp.passengers = []
                    try:
                        self.sheets.upsert_driver_passengers(dp)
                        if old_passengers:
                            self.sheets.clear_rides_with(names=set(old_passengers))
                    except Exception as e:
                        # Откат: восстанавливаем пассажиров
                        try:
                            dp.passengers = old_passengers
                            self.sheets.upsert_driver_passengers(dp)
                        except Exception:
                            pass
                        raise
            except Exception as e:
                await self.log_admin(
                    context, "Sheet write error (weekly answer No)",
                    str(e)[-1500:], update,
                )
                await self._reply(
                    update,
                    "❌ Ошибка при очистке. Обратись к администратору.",
                    reply_markup=self.kb_main(update.effective_user.id),
                )
                return

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

        uid = update.effective_user.id
        await self._reply(
            update,
            t("admin.weekly_choose_mode", tg_id=uid),
            reply_markup=ReplyKeyboardMarkup(
                [
                    [button("btn.admin_mode_tgid", uid)],
                    [button("btn.admin_mode_shift", uid)],
                    [button("btn.cancel", uid)],
                ],
                resize_keyboard=True,
            ),
        )
        return ST_ADMIN_MODE

    async def admin_mode(self, update, context):
        txt = update.message.text
        uid = update.effective_user.id

        if is_button(txt, "btn.admin_mode_tgid"):
            await self._reply(update, t("admin.weekly_enter_tgid", tg_id=uid))
            return ST_ADMIN_TGID

        if is_button(txt, "btn.admin_mode_shift"):
            await self._reply(
                update,
                t("admin.weekly_choose_shift", tg_id=uid),
                reply_markup=ReplyKeyboardMarkup(
                    [
                        [button("btn.shift_day", uid)],
                        [button("btn.shift_night", uid)],
                        [button("btn.shift_meltech_day", uid)],
                        [button("btn.shift_meltech_night", uid)],
                    ],
                    resize_keyboard=True,
                ),
            )
            return ST_ADMIN_SHIFT

        return ConversationHandler.END

    async def admin_tgid(self, update, context):
        raw = update.message.text.strip()
        uid = update.effective_user.id

        if not raw.isdigit():
            await self._reply(
                update,
                t("admin.weekly_tgid_invalid", tg_id=uid),
                reply_markup=self.kb_main(uid),
            )
            return ConversationHandler.END

        tg_id = int(raw)

        if not self.sheets.get_driver(tg_id):
            await self._reply(
                update,
                t("admin.weekly_driver_not_found", tg_id=uid, driver_id=tg_id),
                reply_markup=self.kb_main(uid),
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
            t("admin.weekly_sent_tgid", tg_id=uid, driver_id=tg_id),
            reply_markup=self.kb_main(uid),
        )
        return ConversationHandler.END

    async def admin_shift(self, update, context):
        txt = update.message.text
        if is_button(txt, "btn.shift_day"):
            shift = ShiftType.DAY
        elif is_button(txt, "btn.shift_night"):
            shift = ShiftType.NIGHT
        elif is_button(txt, "btn.shift_meltech_day"):
            shift = ShiftType.MELTECH_DAY
        elif is_button(txt, "btn.shift_meltech_night"):
            shift = ShiftType.MELTECH_NIGHT
        else:
            shift = ShiftType.DAY

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
            t("admin.broadcast_confirm", tg_id=uid, text=text, count=len(driver_tg_ids)),
            reply_markup=self.kb_yes_no(uid),
        )
        return ST_BROADCAST_CONFIRM

    async def broadcast_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return ConversationHandler.END

        if not is_button(update.message.text, "btn.yes"):
            await self._reply(
                update,
                t("admin.broadcast_cancelled", tg_id=uid),
                reply_markup=self.kb_main(uid),
            )
            return ConversationHandler.END

        text = context.user_data.pop("broadcast_text", "")
        if not text:
            await self._reply(
                update,
                t("admin.broadcast_text_lost", tg_id=uid),
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
            await asyncio.sleep(0.1)

        result = f"✅ Отправлено: {sent} водителям."
        if failed:
            result += f"\n❌ Не доставлено: {failed}"

        await self._reply(update, result, reply_markup=self.kb_main(uid))
        await self.log_admin(context, "Broadcast", f"sent={sent} failed={failed} text={text[:100]}", update)
        return ConversationHandler.END

    # ======================================================
    # Expire job (JobQueue)
    # ======================================================

    # ======================================================
    # Language switching
    # ======================================================

    async def set_language_english(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        set_user_lang(uid, "en", self.config.STATE_FILE)
        await self._reply(
            update,
            t("lang.switched_en", tg_id=uid),
            reply_markup=self.kb_main(uid),
        )

    async def set_language_russian(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        set_user_lang(uid, "ru", self.config.STATE_FILE)
        await self._reply(
            update,
            t("lang.switched_ru", tg_id=uid),
            reply_markup=self.kb_main(uid),
        )

    async def expire_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Периодически удаляет водителей, не ответивших на weekly check за 2 часа.

        Запускается через JobQueue каждые 15 минут. Видит тот же bot_state.json,
        что и weekly_answer handler, поэтому pending corrections синхронизированы.
        """
        from weekly import expire_unanswered
        state = get_state_manager(self.config.STATE_FILE)
        try:
            await expire_unanswered(context.bot, self.sheets, state, self.config)
        except Exception as e:
            logger.error("expire_job failed: %s", e)

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