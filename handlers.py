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
from intent import parse_yes_no_intent
from models import Driver, DriverPassengers, Reason, ShiftType, normalize_text
from phones import normalize as normalize_phone
from persistence import get_state_manager

logger = logging.getLogger(__name__)

# Жёсткий предел вместимости карпула — тот же, что в schema БД.
MAX_PASSENGERS = 4


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
    ST_DRIVER_CITY,
    ST_SEARCH_NAME,
    ST_SEARCH_MODE,
    ST_SEARCH_VALUE,
    ST_LEAVE_NAME,
    ST_LEAVE_CONFIRM,
    ST_DRIVER_PHONE,
) = range(20, 37)


# Полные названия штатов США → 2-буквенный код (чтобы понимать «California» = CA).
US_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
    "washington dc": "DC", "washington d.c.": "DC",
}


def _delta(n: int) -> str:
    """Изменение со знаком: «+21», «−4», «без изменений».

    Раньше знак приклеивался шаблоном, и удаление строк печаталось как
    «+-4» — выглядит как опечатка, а не как осмысленный результат.
    """
    if n > 0:
        return f"+{n}"
    if n < 0:
        return f"−{abs(n)}"
    return "без изменений"


def _means_skip(text: str) -> bool:
    """Человек хотел пропустить шаг, но попал не точно в подпись кнопки.

    В живых данных двое так и записались телефоном: один написал
    «Пропустить» словом, вторая отправила «⏭️» — с селектором варианта,
    тогда как в кнопке «⏭» без него. Для строкового сравнения это разные
    значения, и сравнение с подписью кнопки отсекало тех, кто сделал
    почти правильно.
    """
    if is_button(text, "btn.skip"):
        return True
    cleaned = "".join(ch for ch in (text or "") if ch.isalpha()).casefold()
    if cleaned in ("пропустить", "пропуск", "skip"):
        return True
    # Одни лишь значки без букв и цифр — тоже попытка нажать кнопку.
    return bool(text) and not any(ch.isalnum() for ch in text)


class BotHandlers:
    def __init__(self, config, sheets):
        self.config = config
        self.sheets = sheets
        # tg_id -> (момент, водитель ли). Меню зависит от роли, но определять
        # её запросом к Google на каждый ответ нельзя: лимит 60 запросов в
        # минуту мы и так почти выбираем еженедельной рассылкой. Поэтому роль
        # запоминается, когда она и так выяснилась по ходу дела, а если
        # неизвестна — показываем полное меню, как раньше.
        self._role_cache: dict[int, tuple[float, bool]] = {}

    _ROLE_TTL = 15 * 60

    def remember_role(self, tg_id: int | None, is_driver: bool) -> None:
        if tg_id is not None:
            self._role_cache[tg_id] = (time.time(), is_driver)

    def remember_role_and_check(self, tg_id: int) -> bool:
        """Есть ли запись водителя — и сразу запоминаем роль для меню."""
        is_driver = self.sheets.get_driver(tg_id) is not None
        self.remember_role(tg_id, is_driver)
        return is_driver

    def known_role(self, tg_id: int | None) -> Optional[bool]:
        if tg_id is None:
            return None
        entry = self._role_cache.get(tg_id)
        if not entry:
            return None
        stamp, is_driver = entry
        if time.time() - stamp > self._ROLE_TTL:
            self._role_cache.pop(tg_id, None)
            return None
        return is_driver

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

    def kb_main(self, user_id: int | None = None, is_driver: Optional[bool] = None):
        """Главное меню под роль пользователя.

        Раньше клавиатура была одна на всех: водители видели «Стать
        водителем», а пассажиры — «Добавить пассажиров» и упирались в
        «Ты не зарегистрирован как водитель». Теперь человек видит только то,
        что ему доступно.

        Роль берём из кэша; если она неизвестна — показываем полный набор,
        чтобы ничего не пропало.
        """
        if is_driver is None:
            is_driver = self.known_role(user_id)

        b = lambda key: button(key, user_id)

        if is_driver is True:
            keyboard = [
                [b("btn.add_passengers"), b("btn.remove_passenger")],
                [b("btn.my_record"), b("btn.find_driver")],
                [b("btn.stop_being_driver")],
            ]
        elif is_driver is False:
            keyboard = [
                [b("btn.become_driver")],
                [b("btn.find_driver"), b("btn.my_record")],
                [b("btn.leave_carpool")],
            ]
        else:
            keyboard = [
                [b("btn.become_driver"), b("btn.add_passengers")],
                [b("btn.my_record"), b("btn.remove_passenger")],
                [b("btn.find_driver"), b("btn.leave_carpool")],
                [b("btn.stop_being_driver")],
            ]

        if user_id is not None and user_id in self.config.ADMIN_USER_IDS:
            keyboard.append([b("btn.admin_weekly_target")])

        keyboard.append([b("btn.help")])

        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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

        effective_message, а не message: при РЕДАКТИРОВАНИИ сообщения Telegram
        присылает edited_message, и update.message оказывается None — команда
        падала с AttributeError вместо ответа.
        """
        message = update.effective_message
        if message is None:
            logger.warning("нет effective_message, отвечать некуда: %s", text[:80])
            return None
        return await message.reply_text(text, **kwargs)


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
        if update.effective_message and update.effective_message.text:
            txt = update.effective_message.text

        # Антифлуд: не чаще 1 unknown/20сек на пользователя
        if not self._throttle(context, f"unknown:{u.id}", 20):
            return

        await self.log_admin(
            context,
            "Unknown message",
            f"text={txt!r}"[:1500],
            update,
        )

        if update.effective_message:
            await self._reply(
                update,
                t("unknown.message", tg_id=u.id),
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
        uid = update.effective_user.id
        # Один запрос на осознанное действие пользователя — приемлемо, зато
        # дальше меню и подсказки сразу под его роль.
        try:
            is_driver = await asyncio.to_thread(self.remember_role_and_check, uid)
        except Exception:
            is_driver = None

        role_hint = t(
            "start.role_driver" if is_driver else "start.role_passenger", tg_id=uid
        ) if is_driver is not None else ""

        await self._reply(
            update,
            t("start.greeting", tg_id=uid, role_hint=role_hint),
            reply_markup=self.kb_main(uid, is_driver),
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await self._reply(
            update,
            t("cancel.done", tg_id=update.effective_user.id),
            reply_markup=self.kb_main(update.effective_user.id),
        )
        return ConversationHandler.END

    # ======================================================
    # Become driver
    # ======================================================

    async def become_driver_start(self, update, context):
        await self._reply(
            update,
            t("driver.enter_name", tg_id=update.effective_user.id),
        )
        return ST_DRIVER_NAME

    async def become_driver_name(self, update, context):
        tg_id = update.effective_user.id
        name = update.effective_message.text.strip()
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
            if suggestions:
                msg = t(
                    "driver.name_suggestions", tg_id=tg_id,
                    suggestions="\n".join(f"• {s}" for s in suggestions),
                )
            else:
                msg = t("driver.name_not_in_employees", tg_id=tg_id)
            await self._reply(
                update,
                msg,
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Двойная роль разрешена: сотрудник может быть пассажиром у другого водителя
        # и при этом зарегистрироваться водителем (пока у него нет своих пассажиров).
        # Как только он добавит своих пассажиров — авто-отвязка уберёт его из чужого
        # карпула. Поэтому блок «ты пассажир — нельзя стать водителем» убран.

        # Защита: проверяем, не зарегистрирован ли уже другой водитель с этим именем
        if self.sheets.is_name_taken_by_other_driver(emp.name, tg_id):
            await self._reply(
                update,
                t("driver.name_taken_by_other", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        context.user_data["driver_name"] = emp.name
        await self._reply(update, t("driver.enter_car", tg_id=tg_id))
        return ST_DRIVER_CAR

    async def become_driver_car(self, update, context):
        context.user_data["driver_car"] = update.effective_message.text.strip()
        await self._reply(
            update, t("driver.enter_plates", tg_id=update.effective_user.id),
        )
        return ST_DRIVER_PLATES

    async def become_driver_plates(self, update, context):
        context.user_data["driver_plates"] = update.effective_message.text.strip()
        await self._reply(update, t("driver.ask_city", tg_id=update.effective_user.id))
        return ST_DRIVER_CITY

    async def become_driver_city(self, update, context):
        tg_id = update.effective_user.id
        raw = update.effective_message.text.strip()
        matches = self.sheets.find_cities(raw)
        if not matches:
            all_cities = [f"{c}, {s}" if s else c for c, s in self.sheets.cities()]
            suggestions = difflib.get_close_matches(raw, all_cities, n=5, cutoff=0.5)
            msg = t("driver.city_not_found", tg_id=tg_id)
            if suggestions:
                msg += "\n" + "\n".join(f"• {s}" for s in suggestions)
            await self._reply(update, msg)
            return ST_DRIVER_CITY
        if len(matches) > 1:
            states = ", ".join(s for _, s in matches)
            await self._reply(update, t(
                "city.ambiguous", tg_id=tg_id, city=matches[0][0],
                states=states, example=f"{matches[0][0]}, {matches[0][1]}",
            ))
            return ST_DRIVER_CITY

        city, state = matches[0]
        context.user_data["driver_city"] = city
        context.user_data["driver_state"] = state
        kb = ReplyKeyboardMarkup(
            [[button("btn.skip", tg_id)]], resize_keyboard=True, one_time_keyboard=True,
        )
        await self._reply(update, t("driver.ask_phone", tg_id=tg_id), reply_markup=kb)
        return ST_DRIVER_PHONE

    async def become_driver_phone(self, update, context):
        """Телефон — по желанию.

        Требовать личный номер не хотим, но без него у водителя без
        @username в карточке поиска не остаётся никакого контакта.
        Поэтому предлагаем, а не обязываем.
        """
        tg_id = update.effective_user.id
        raw = (update.effective_message.text or "").strip()
        skipped = _means_skip(raw)
        if skipped:
            phone = ""
        else:
            phone = normalize_phone(raw) or ""
            if not phone:
                # Не сохраняем молча: иначе человек уверен, что оставил
                # контакт, а в карточке его нет.
                await self._reply(
                    update,
                    t("driver.phone_invalid", tg_id=tg_id),
                    reply_markup=ReplyKeyboardMarkup(
                        [[button("btn.skip", tg_id)]],
                        resize_keyboard=True, one_time_keyboard=True,
                    ),
                )
                return ST_DRIVER_PHONE

        city = context.user_data.get("driver_city", "")
        state = context.user_data.get("driver_state", "")
        driver = Driver(
            name=context.user_data["driver_name"],
            tg_id=tg_id,
            username=(update.effective_user.username or ""),
            car=context.user_data["driver_car"],
            plates=context.user_data["driver_plates"],
            city=city,
            state=state,
            phone=phone,
        )
        try:
            self.sheets.upsert_driver(driver)
        except Exception as e:
            await self.log_admin(
                context, "Sheet write error (upsert driver)", str(e)[-1500:], update,
            )
            await self._reply(
                update, t("driver.register_error", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        await self.log_admin(
            context, "Driver created/updated",
            f"{driver.name} ({tg_id}) {city}, {state}", update,
        )
        self.remember_role(tg_id, True)
        note = t("driver.phone_skipped" if skipped else "driver.phone_saved", tg_id=tg_id)
        await self._reply(
            update,
            t("driver.saved", tg_id=tg_id, city=city, state=state, note=note,
              button=button("btn.add_passengers", tg_id)),
            reply_markup=self.kb_main(tg_id, True),
        )
        context.user_data.clear()
        return ConversationHandler.END

    # ======================================================
    # My record
    # ======================================================

    async def my_record(self, update, context):
        tg_id = update.effective_user.id
        driver = self.sheets.get_driver(tg_id)
        self.remember_role(tg_id, driver is not None)

        if not driver:
            await self._reply(
                update,
                t("my_record.empty", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return

        # Проверяем консистентность смен перед показом
        shift_removed = self.sheets.enforce_shift_consistency(tg_id)

        dp = self.sheets.get_driver_passengers(tg_id)
        passengers = dp.passengers if dp else []

        txt = ""
        if shift_removed:
            txt += t(
                "passengers.shift_cleanup", tg_id=tg_id,
                names="\n".join(f"• {n}" for n in shift_removed),
            ) + "\n\n"
            await self.log_admin(
                context, "Shift consistency cleanup (my_record)",
                f"driver_tgid={tg_id} removed={shift_removed}", update,
            )

        txt += t(
            "my_record.text", tg_id=tg_id,
            name=driver.name, car=driver.car, plates=driver.plates,
        ) + "\n\n"
        if passengers:
            txt += t(
                "my_record.passengers", tg_id=tg_id,
                passengers="\n".join(f"  {i+1}. {p}" for i, p in enumerate(passengers)),
            )
        else:
            txt += t("my_record.no_passengers", tg_id=tg_id)

        await self._reply(update, txt, reply_markup=self.kb_main(update.effective_user.id))

    # ======================================================
    # Stop being driver
    # ======================================================

    async def stop_being_driver_start(self, update, context):
        tg_id = update.effective_user.id
        if not self.remember_role_and_check(tg_id):
            await self._reply(
                update,
                t("my_record.empty", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        await self._reply(
            update,
            t("stop_driver.confirm", tg_id=tg_id),
            reply_markup=self.kb_yes_no(tg_id),
        )
        return ST_STOP_CONFIRM

    async def stop_being_driver_confirm(self, update, context):
        tg_id = update.effective_user.id
        intent = parse_yes_no_intent(update.effective_message.text or "")

        if intent == "unclear":
            await self._reply(
                update,
                t("stop_driver.unclear", tg_id=tg_id),
                reply_markup=self.kb_yes_no(update.effective_user.id),
            )
            return ST_STOP_CONFIRM

        if intent == "yes":
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
                    t("stop_driver.error", tg_id=tg_id),
                    reply_markup=self.kb_main(update.effective_user.id),
                )
                return ConversationHandler.END

            # Роль изменилась — кэш обязан узнать об этом сразу. Иначе
            # known_role ещё 15 минут (TTL) отвечает «водитель», и человек
            # получает меню с кнопками, которые ему больше не доступны.
            self.remember_role(tg_id, False)

            await self.log_admin(
                context,
                "Driver stopped being driver",
                f"tg_id={tg_id}\npassengers={len(passenger_names)}",
                update,
            )
            await self._reply(
                update,
                t("stop_driver.done", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
        else:
            await self._reply(
                update,
                t("stop_driver.nothing", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )

        return ConversationHandler.END

    # ======================================================
    # Add passengers
    # ======================================================

    async def add_passengers_start(self, update, context):
        tg_id = update.effective_user.id
        if not self.remember_role_and_check(tg_id):
            await self._reply(
                update,
                t("passengers.not_a_driver", tg_id=tg_id,
                  button=button("btn.become_driver", tg_id)),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Проверяем консистентность смен перед добавлением
        shift_removed = self.sheets.enforce_shift_consistency(tg_id)
        prefix = ""
        if shift_removed:
            prefix = t(
                "passengers.shift_cleanup", tg_id=tg_id,
                names="\n".join(f"• {n}" for n in shift_removed),
            ) + "\n\n"
            await self.log_admin(
                context, "Shift consistency cleanup (add_passengers)",
                f"driver_tgid={tg_id} removed={shift_removed}", update,
            )

        await self._reply(
            update,
            prefix + t("passengers.enter", tg_id=tg_id),
        )
        return ST_ADD_PASSENGERS

    # Коды причин → текст. Подсказка про самостоятельное открепление
    # прицепляется здесь, а не в локали: ей нужно название кнопки, а оно
    # зависит от языка читателя — слой данных его не знает.
    _REASON_HINTS = {"passenger_warning.already_with_driver": "validate.taken_hint"}

    def _render_reasons(self, reasons, uid) -> list[str]:
        out = []
        for r in reasons:
            text = t(r.code, tg_id=uid, **r.params)
            hint_key = self._REASON_HINTS.get(r.code)
            if hint_key:
                text += " " + t(hint_key, tg_id=uid,
                                button=button("btn.leave_carpool", uid))
            out.append(text)
        return out

    async def add_passengers_input(self, update, context):
        tg_id = update.effective_user.id
        names = [
            x.strip()
            for x in update.effective_message.text.splitlines()
            if x.strip()
        ]

        valid, errors, warnings = self.sheets.validate_passengers(tg_id, names)

        if errors:
            text = "\n\n".join(self._render_reasons(errors, tg_id))
            # «Никого не удалось добавить» без подробностей бесполезно:
            # водителю нужно знать, кто именно и почему не прошёл.
            if warnings and any(r.code == "validate.nobody_added" for r in errors):
                text += "\n\n" + t(
                    "validate.nobody_added_details", tg_id=tg_id,
                    details="\n".join(self._render_reasons(warnings, tg_id)),
                )
            await self._reply(
                update, text,
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Нет новых валидных пассажиров — НЕ трогаем существующих
        if not valid:
            parts = [t("passengers.nothing_added", tg_id=tg_id)]
            if warnings:
                parts.append("\n".join(self._render_reasons(warnings, tg_id)))
            await self._reply(
                update,
                "\n\n".join(parts),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        driver = self.sheets.get_driver(tg_id)
        self.remember_role(tg_id, driver is not None)

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
                warnings.append(Reason("passengers.max_reached", {"name": name}))

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
                t("passengers.error", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return ConversationHandler.END

        # Авто-отвязка: если этот водитель сам числился пассажиром у кого-то —
        # он «пересел за руль», убираем его из чужого карпула и уведомляем того водителя.
        try:
            unlinked = self.sheets.unlink_passenger_everywhere(driver.name, except_tgid=tg_id)
        except Exception:
            unlinked = None
        if unlinked:
            other_tgid, other_name = unlinked
            try:
                await context.bot.send_message(
                    chat_id=other_tgid,
                    text=t("passengers.auto_unlink_notice", tg_id=other_tgid,
                           name=driver.name),
                    reply_markup=self.kb_main(other_tgid),
                )
            except Exception:
                pass
            await self.log_admin(
                context, "Auto-unlink (driver became active)",
                f"{driver.name} убран из карпула {other_name} (tg={other_tgid})", update,
            )

        await self.log_admin(
            context, "Passengers updated",
            f"Driver {driver.name}\nAll: {', '.join(merged)}\nNew: {', '.join(new_names)}",
            update,
        )
        parts = [t("passengers.saved", tg_id=tg_id)]
        parts.append(t("passengers.added", tg_id=tg_id,
                       names="\n".join(f"• {n}" for n in new_names)))

        if warnings:
            parts.append(t("passengers.skipped", tg_id=tg_id,
                           names="\n".join(self._render_reasons(warnings, tg_id))))

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
                t("passengers.shift_cleanup", tg_id=tg_id,
                  names="\n".join(f"• {n}" for n in shift_removed)),
            )

        dp = self.sheets.get_driver_passengers(tg_id)

        if not dp or not dp.passengers:
            await self._reply(
                update,
                t("remove_passenger.no_passengers", tg_id=tg_id),
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
            t("remove_passenger.choose", tg_id=tg_id),
            reply_markup=kb,
        )
        return ST_REMOVE_PASSENGER

    async def remove_passenger_input(self, update, context):
        tg_id = update.effective_user.id
        chosen = update.effective_message.text.strip()

        # Получаем актуальный список из sheets (не из кэша user_data)
        dp = self.sheets.get_driver_passengers(tg_id)
        if not dp:
            await self._reply(
                update,
                t("remove_passenger.no_data", tg_id=tg_id),
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
                t("remove_passenger.not_found", tg_id=tg_id),
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
                t("remove_passenger.error", tg_id=tg_id),
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
                t("remove_passenger.done", tg_id=tg_id, name=match,
                  remaining=remaining),
                reply_markup=self.kb_main(update.effective_user.id),
            )
        else:
            await self._reply(
                update,
                t("remove_passenger.done_empty", tg_id=tg_id, name=match),
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

        pax_text = (
            "\n".join(passengers) if passengers
            else t("weekly.no_passengers", tg_id=tg_id)
        )
        txt = t("weekly.greeting", tg_id=tg_id, passengers=pax_text)

        await self.log_admin(
            context,
            "Weekly send",
            f"tg_id={tg_id} shift={shift} passengers={len(passengers)}",
        )

        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=txt,
                reply_markup=self.kb_yes_no(tg_id),
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

        text = update.effective_message.text or ""
        intent = parse_yes_no_intent(text)

        if intent == "yes":
            state.remove_pending(tg_id)
            await self.log_admin(
                context, "Weekly ответ", f"✅ Да (text={text!r})", update,
            )
            await self._reply(
                update,
                t("weekly.yes_answer", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return

        if intent == "no":
            try:
                dp = self.sheets.get_driver_passengers(tg_id)
                if dp:
                    old_passengers = dp.passengers[:]
                    dp.passengers = []
                    try:
                        self.sheets.upsert_driver_passengers(dp)
                        if old_passengers:
                            self.sheets.clear_rides_with(names=set(old_passengers))
                    except Exception:
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
                    t("weekly.error", tg_id=tg_id),
                    reply_markup=self.kb_main(update.effective_user.id),
                )
                return

            state.remove_pending(tg_id)
            await self.log_admin(
                context, "Weekly ответ", f"❌ Нет — очистка (text={text!r})", update,
            )
            await self._reply(
                update,
                t("weekly.no_answer", tg_id=tg_id),
                reply_markup=self.kb_main(update.effective_user.id),
            )
            return

        # intent == "unclear" — pending state stays so the user gets re-asked.
        # Anti-flood: throttled admin log to avoid spam from chatty users.
        if self._throttle(context, f"weekly_unclear:{tg_id}", 60):
            await self.log_admin(
                context, "Weekly ответ unclear", f"text={text!r}"[:1500], update,
            )
        dp = self.sheets.get_driver_passengers(tg_id)
        passengers = dp.passengers if dp else []
        pax_text = "\n".join(passengers) if passengers else t("weekly.no_passengers", tg_id=tg_id)
        await self._reply(
            update,
            t("weekly.unclear", tg_id=tg_id) + "\n\n" +
            t("weekly.greeting", tg_id=tg_id, passengers=pax_text),
            reply_markup=self.kb_yes_no(tg_id),
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
                t("admin.not_authorized", tg_id=uid),
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
        txt = update.effective_message.text
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
        raw = update.effective_message.text.strip()
        uid = update.effective_user.id

        if not raw.isdigit():
            await self._reply(
                update,
                t("admin.weekly_tgid_invalid", tg_id=uid),
                reply_markup=self.kb_main(uid),
            )
            return ConversationHandler.END

        tg_id = int(raw)

        if not self.remember_role_and_check(tg_id):
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
        txt = update.effective_message.text
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
                t("generic.error", tg_id=update.effective_user.id),
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
            t("admin.weekly_sent_shift", tg_id=update.effective_user.id,
              count=len(tgids), shift=shift.to_display()),
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
                    text=t("admin.keyboard_update", tg_id=tg_id),
                    reply_markup=self.kb_main(tg_id),
                )
                sent += 1
            except Exception:
                failed += 1

        result = t("admin.broadcast_keyboard_done", tg_id=uid, sent=sent)
        if failed:
            result += t("admin.broadcast_keyboard_failed", tg_id=uid, failed=failed)
        await self._reply(update, result, reply_markup=self.kb_main(uid))

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
                t("admin.broadcast_usage", tg_id=uid),
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

        intent = parse_yes_no_intent(update.effective_message.text or "")
        if intent == "unclear":
            await self._reply(
                update,
                t("admin.broadcast_unclear", tg_id=uid),
                reply_markup=self.kb_yes_no(uid),
            )
            return ST_BROADCAST_CONFIRM
        if intent == "no":
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

        result = t("admin.broadcast_result", tg_id=uid, sent=sent)
        if failed:
            result += t("admin.broadcast_failed_line", tg_id=uid, failed=failed)

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
                t("admin.report_not_found", tg_id=uid),
                reply_markup=self.kb_main(uid),
            )
            return

        if not svodka_values or len(svodka_values) < 2:
            await self._reply(
                update,
                t("admin.report_empty", tg_id=uid),
                reply_markup=self.kb_main(uid),
            )
            return

        header = svodka_values[0]
        label_a = header[1] if len(header) > 1 else "Week A"
        label_b = header[2] if len(header) > 2 else "Week B"

        lines = [t("admin.report_header", tg_id=uid,
                   label_a=label_a, label_b=label_b)]
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
    # ======================================================
    # Поиск водителя (rideshare) — только для сотрудников
    # ======================================================

    async def find_driver_start(self, update, context):
        tg_id = update.effective_user.id
        verified = context.bot_data.setdefault("verified_searchers", set())
        if tg_id in verified:
            return await self._search_ask_mode(update, context)
        await self._reply(update, t("search.ask_name", tg_id=tg_id))
        return ST_SEARCH_NAME

    async def search_name(self, update, context):
        tg_id = update.effective_user.id
        name = update.effective_message.text.strip()
        emp = self.sheets.get_employee_by_name(name)
        if not emp:
            await self._reply(
                update, t("search.not_employee", tg_id=tg_id),
                reply_markup=self.kb_main(tg_id),
            )
            return ConversationHandler.END
        context.bot_data.setdefault("verified_searchers", set()).add(tg_id)
        return await self._search_ask_mode(update, context)

    async def _search_ask_mode(self, update, context):
        tg_id = update.effective_user.id
        kb = ReplyKeyboardMarkup(
            [[button("btn.by_city", tg_id), button("btn.by_state", tg_id)],
             [button("btn.cancel", tg_id)]],
            resize_keyboard=True, one_time_keyboard=True,
        )
        await self._reply(update, t("search.choose_mode", tg_id=tg_id), reply_markup=kb)
        return ST_SEARCH_MODE

    async def search_mode(self, update, context):
        tg_id = update.effective_user.id
        txt = update.effective_message.text or ""
        low = txt.casefold()
        if is_button(txt, "btn.by_city") or "город" in low or "city" in low:
            context.user_data["search_mode"] = "city"
            await self._reply(update, t("search.ask_city", tg_id=tg_id))
        elif is_button(txt, "btn.by_state") or "штат" in low or "state" in low:
            context.user_data["search_mode"] = "state"
            await self._reply(update, t("search.ask_state", tg_id=tg_id))
        else:
            await self._reply(update, t("search.choose_mode", tg_id=tg_id))
            return ST_SEARCH_MODE
        return ST_SEARCH_VALUE

    _SHIFT_KEYS = {
        ShiftType.DAY: "shift.day",
        ShiftType.NIGHT: "shift.night",
        ShiftType.MELTECH_DAY: "shift.meltech_day",
        ShiftType.MELTECH_NIGHT: "shift.meltech_night",
    }

    def _driver_card(self, d, viewer_id=None, taken: Optional[int] = None) -> str:
        """Карточка водителя в выдаче поиска.

        Показываем смену и свободные места: без них пассажир пишет тому, у
        кого машина уже полная, или ночнику, работая в день. Данные у нас
        есть, скрывать их незачем.
        """
        head = f"👤 {d.name}"
        shift_key = self._SHIFT_KEYS.get(ShiftType.from_string(d.shift or ""))
        if shift_key:
            head += f" · {t(shift_key, tg_id=viewer_id)}"

        parts = [head]

        second = []
        if d.car:
            second.append(f"🚗 {d.car}")
        if taken is not None:
            free = max(0, MAX_PASSENGERS - taken)
            second.append(
                t("card.seats_free", tg_id=viewer_id, free=free, total=MAX_PASSENGERS)
                if free else t("card.seats_full", tg_id=viewer_id)
            )
        if second:
            parts.append(" · ".join(second))

        contact = []
        if d.username:
            contact.append(f"t.me/{d.username}")
        # Колонку телефона заполняют руками, и туда попадает что угодно —
        # у одного водителя там оказался список пассажиров. Показываем
        # только то, что разобралось как номер.
        phone = normalize_phone(d.phone)
        if phone:
            contact.append(f"📞 {phone}")
        parts.append(" · ".join(contact) if contact
                     else t("card.no_contact", tg_id=viewer_id))
        return "\n".join(parts)

    async def search_value(self, update, context):
        tg_id = update.effective_user.id
        mode = context.user_data.get("search_mode", "city")
        raw = update.effective_message.text.strip()

        if mode == "city":
            matches = self.sheets.find_cities(raw)
            if not matches:
                all_cities = [f"{c}, {s}" if s else c for c, s in self.sheets.cities()]
                sugg = difflib.get_close_matches(raw, all_cities, n=5, cutoff=0.5)
                msg = t("search.city_not_found", tg_id=tg_id)
                if sugg:
                    msg += "\n" + "\n".join(f"• {s}" for s in sugg)
                await self._reply(update, msg)
                return ST_SEARCH_VALUE
            if len(matches) > 1:
                states = ", ".join(s for _, s in matches)
                await self._reply(update, t(
                    "city.ambiguous", tg_id=tg_id, city=matches[0][0],
                    states=states, example=f"{matches[0][0]}, {matches[0][1]}",
                ))
                return ST_SEARCH_VALUE
            city, state = matches[0]
            drivers = self.sheets.find_drivers(city=city)
            where = f"{city}, {state}" if state else city
        else:
            # Принимаем и 2-буквенный код (CA), и полное название (California).
            code = US_STATE_NAMES.get(raw.strip().casefold(), raw.strip().upper())
            states = {s.upper(): s for s in self.sheets.states()}
            if code not in states:
                await self._reply(update, t("search.state_not_found", tg_id=tg_id))
                return ST_SEARCH_VALUE
            drivers = self.sheets.find_drivers(state=states[code])
            where = states[code]

        drivers = [d for d in drivers if int(d.tg_id) != int(tg_id)]
        if not drivers:
            await self._reply(
                update, t("search.none", tg_id=tg_id, where=where),
                reply_markup=self.kb_main(tg_id),
            )
            return ConversationHandler.END

        try:
            counts = await asyncio.to_thread(self.sheets.carpool_counts)
        except Exception:
            counts = {}

        # Сначала те, у кого есть свободные места.
        drivers.sort(key=lambda d: counts.get(int(d.tg_id), 0) >= MAX_PASSENGERS)

        header = t("search.header", tg_id=tg_id, where=where)
        text = header + "\n\n" + "\n\n".join(
            self._driver_card(d, viewer_id=tg_id, taken=counts.get(int(d.tg_id)))
            for d in drivers
        )
        # Telegram лимит 4096 — режем на части
        for i in range(0, len(text), 4000):
            await self._reply(
                update, text[i:i + 4000],
                reply_markup=self.kb_main(tg_id) if i + 4000 >= len(text) else None,
            )
        return ConversationHandler.END

    # ======================================================
    # Захват снапшотов в Postgres (admin only)
    #
    # Работает параллельно с GAS и ничего в Sheets не меняет. Команды нужны
    # потому, что Postgres на Railway доступен только изнутри приватной сети:
    # запустить backfill с ноутбука нельзя, а из бота — можно.
    # ======================================================

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Указатель по админским командам. /admin"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return
        import admin_commands

        await self._reply(update, admin_commands.render()[:4000],
                          reply_markup=self.kb_main(uid))

    async def whois_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Всё про человека. /whois <имя или telegram id>"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        query = " ".join(context.args or []).strip()
        if not query:
            await self._reply(
                update,
                "/whois <имя или telegram id>\n\nПример: /whois Akmal Shah",
                reply_markup=self.kb_main(uid),
            )
            return

        from db import read as db_read

        try:
            info = await asyncio.to_thread(db_read.whois, query)
        except Exception as e:
            logger.exception("whois failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if info is None:
            await self._reply(update, "⚪️ БД выключена.", reply_markup=self.kb_main(uid))
            return
        if not info.get("found"):
            await self._reply(
                update,
                f"Не нашёл «{query}».\n\nИмя сверяется без учёта порядка слов "
                "и регистра, так что дело скорее в написании.",
                reply_markup=self.kb_main(uid),
            )
            return

        смена = {"day": "☀️ день", "night": "🌙 ночь"}.get(info["shift"], "не указана")
        lines = [f"👤 {info['name']}", "",
                 f"Смена: {смена}",
                 f"Объект: {info['site'] or 'не определён'}"]
        if info["telegram_id"]:
            lines.append(f"Telegram ID: {info['telegram_id']}")
        lines.append(f"Отметок в табелях: {info['presence_days']}"
                     + (f", последняя {info['last_seen']}" if info["last_seen"] else ""))

        if info["drives"]:
            lines.append(f"\n🚗 Сегодня везёт ({len(info['drives'])}):")
            for n in info["drives"]:
                lines.append(f"  • {n}")
        if info["rides_with"]:
            водитель, tg = info["rides_with"]
            lines.append(f"\n🧍 Сегодня едет с: {водитель}"
                         + (f" (id {tg})" if tg else ""))
        if not info["drives"] and not info["rides_with"]:
            lines.append("\nСегодня ни с кем не связан.")

        if info["history"]:
            lines.append("\nПоследние поездки пассажиром:")
            for дата, водитель in info["history"]:
                lines.append(f"  {дата} — {водитель}")

        await self._reply(update, "\n".join(lines)[:4000],
                          reply_markup=self.kb_main(uid))

    async def unlink_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Открепить пассажира от его водителя. /unlink <имя>"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        name = " ".join(context.args or []).strip()
        if not name:
            await self._reply(
                update,
                "/unlink <имя пассажира>\n\n"
                "Освобождает человека: его сможет записать другой водитель.",
                reply_markup=self.kb_main(uid),
            )
            return

        try:
            # except_tgid=0 — ни один водитель не имеет такого id,
            # значит открепляем от кого угодно.
            hit = await asyncio.to_thread(
                self.sheets.unlink_passenger_everywhere, name, except_tgid=0
            )
        except Exception as e:
            logger.exception("unlink failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if not hit:
            await self._reply(
                update, f"«{name}» ни к кому не записан — освобождать нечего.",
                reply_markup=self.kb_main(uid),
            )
            return

        tg_id, водитель = hit
        await self.log_admin(
            context, "Admin unlink",
            f"{name} откреплён от {водитель} (tg={tg_id})", update,
        )
        await self._reply(
            update,
            f"✅ «{name}» откреплён от водителя {водитель}.\n\n"
            "Теперь его может записать любой другой.",
            reply_markup=self.kb_main(uid),
        )

    async def db_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Состояние захвата. /db_status"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        from db import store

        try:
            info = await asyncio.to_thread(store.status)
        except Exception as e:
            await self._reply(update, f"❌ Ошибка БД: {e}", reply_markup=self.kb_main(uid))
            return

        if not info.get("enabled"):
            await self._reply(
                update,
                "⚪️ Захват выключен: переменная DATABASE_URL не задана.",
                reply_markup=self.kb_main(uid),
            )
            return

        lines = [
            "🗄 Захват снапшотов",
            "",
            f"Строк: {info['rows']}",
            f"Водителей: {info['drivers']}",
            f"Дней истории: {info['days']}",
            f"Период: {info['first']} — {info['last']}",
        ]

        gaps = info.get("gaps") or []
        if gaps:
            lines.append(f"\n⚠️ ПРОПУЩЕНО ДНЕЙ: {len(gaps)}")
            if len(gaps) <= 14:
                lines.append("  " + ", ".join(str(d) for d in gaps))
            else:
                lines.append(f"  {gaps[0]} … {gaps[-1]}")
        else:
            lines.append("\n✅ Пропусков нет, история непрерывна")

        per_day = info.get("per_day") or []
        if per_day:
            lines.append("\nПо дням (водителей):")
            for d, cnt in per_day:
                lines.append(f"  {d}: {cnt}")
        if info["runs"]:
            lines.append("\nПоследние прогоны:")
            for source, started, written, updated, skipped, error in info["runs"]:
                stamp = started.strftime("%m-%d %H:%M")
                if error:
                    lines.append(f"• {stamp} {source} — ОШИБКА: {error[:80]}")
                else:
                    lines.append(
                        f"• {stamp} {source} — записано {written}, "
                        f"обновлено {updated}, пропущено {skipped}"
                    )
        await self._reply(update, "\n".join(lines), reply_markup=self.kb_main(uid))

    async def db_capture_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Снять снимок текущих карпулов вручную. /db_capture"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return
        await self._run_capture(
            update, uid,
            lambda: __import__("db.store", fromlist=["store"]).capture_live(
                self.sheets, self.config.DRIVERS_PASSENGERS_SHEET
            ),
            "снимок на сегодня",
        )

    async def db_backfill_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Перенести историю из week-листа. /db_backfill week2"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        args = context.args or []
        if not args:
            await self._reply(
                update,
                "Укажи лист: /db_backfill week2\n"
                "Доступны week1, week2, week3, week4.",
                reply_markup=self.kb_main(uid),
            )
            return

        sheet = args[0].strip()
        if sheet not in {"week1", "week2", "week3", "week4"}:
            await self._reply(
                update, f"Неизвестный лист «{sheet}».", reply_markup=self.kb_main(uid)
            )
            return

        await self._run_capture(
            update, uid,
            lambda: __import__("db.store", fromlist=["store"]).backfill(self.sheets, sheet),
            f"перенос {sheet}",
        )

    async def db_import_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Импорт ростера и присутствия из табелей. /db_import [подстрока]

        Без аргумента читает ВСЕ листы-табели, а это по одному запросу к Google
        на лист. Квота около 60 запросов в минуту почти выбирается еженедельной
        рассылкой, поэтому полный прогон — в спокойное время, а точечный через
        аргумент: /db_import BUFFALO.
        """
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        args = context.args or []
        raw = args[0].strip() if args else ""
        roster_only = raw.casefold() in ("ростер", "roster")
        only = None if (not raw or roster_only) else raw
        label = ("синхронизация ростера" if roster_only
                 else f"импорт «{only}»" if only else "импорт всех табелей")
        await self._reply(update, f"⏳ {label}… при полном прогоне это минута-две.")

        from db import roster

        try:
            info = await asyncio.to_thread(
                lambda: roster.import_roster(
                    self.sheets, drivers_sheet=self.config.DRIVERS_SHEET,
                    only=only, roster_only=roster_only)
            )
        except Exception as e:
            logger.exception("import failed")
            await self._reply(
                update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid)
            )
            return

        if not info.get("enabled"):
            await self._reply(
                update,
                "⚪️ БД выключена: переменная DATABASE_URL не задана.",
                reply_markup=self.kb_main(uid),
            )
            return

        if not info["sheets"] and not roster_only:
            await self._reply(
                update,
                "Ни один лист не опознан как табель.\n"
                "Табель = объект в конце имени и разбираемый диапазон дат.",
                reply_markup=self.kb_main(uid),
            )
            return

        lines = [
            f"✅ {label} — готово",
            "",
            f"Табелей прочитано: {info['sheets']}",
            f"Объектов: {info['sites_total']} ({_delta(info['sites_new'])})",
            f"Людей: {info['people_total']} ({_delta(info['people_new'])}), "
            f"в прогоне {info['people_seen']}",
            f"Присутствие: {info['presence_total']} "
            f"({_delta(info['presence_new'])}) "
            f"из {info['presence_read']} прочитанных",
            f"Связей водитель↔пассажир: {info['carpool_total']} "
            f"(собрано {info['carpool_built']})",
        ]

        # Несвязанное важнее собранного: это имена, которые надо починить
        # в источнике, иначе человек не попадёт в расчёт.
        problems = info["carpool_problems"]
        labels = {
            "passenger": "пассажир не найден в ростере",
            "driver": "водитель не найден в ростере",
            "self": "водитель записан сам себе в пассажиры",
            "taken": "пассажир уже у другого водителя в тот же день",
        }
        if any(problems.values()):
            lines.append("\n⚠️ Не связалось:")
            for kind, label in labels.items():
                items = problems.get(kind) or []
                if not items:
                    continue
                # Людей, а не строк: «59» читается как 59 разных человек,
                # хотя за ними стоят трое, ездящие каждый день. Админу важно
                # именно число людей — это объём ручной работы.
                names = sorted({str(i[1]) for i in items if len(i) > 1})
                lines.append(
                    f"  • {label}: {len(names)} чел. (записей {len(items)})"
                )
                if names:
                    shown = ", ".join(names[:5])
                    lines.append(f"    {shown}" + (" …" if len(names) > 5 else ""))

        if info["orphans"]:
            lines.append(f"\n⚠️ Строк без человека: {info['orphans']}")

        # Конфликт telegram_id почти всегда означает переименование с
        # оставшейся старой строкой в drivers — это надо починить в источнике.
        conflicts = info["tg_conflicts"]
        if conflicts:
            lines.append(f"\n⚠️ Один telegram_id на двух людей: {len(conflicts)}")
            for owner, loser, tg in conflicts[:8]:
                lines.append(f"  • {tg}: «{owner}» ← оставлен, «{loser}» ← снят")

        # Молчаливо пропущенный табель = неделя без присутствия и без доплат.
        skipped = info.get("skipped_sheets") or []
        if skipped:
            lines.append(f"\n⚠️ Похожи на табель, но пропущены: {len(skipped)}")
            for title, reason in skipped[:6]:
                lines.append(f"  • «{title}» — {reason}")

        per_sheet = info["per_sheet"]
        if per_sheet and len(per_sheet) <= 12:
            lines.append("\nПо листам (отметок):")
            for title, count in sorted(per_sheet.items()):
                lines.append(f"  {title}: {count}")

        lines.append(
            "\nℹ️ Бот из этих таблиц пока не читает — на работу людей импорт "
            "не влияет. Смотреть и править можно в админке."
        )
        text = "\n".join(lines)
        await self._reply(update, text[:4000], reply_markup=self.kb_main(uid))

    async def db_diff_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сверить состав карпулов в таблице и в базе. /db_diff

        Это условие перехода на чтение из базы: пока картины расходятся,
        переключать нельзя. Сравнение идёт по нормализованному имени —
        «Akmal Shah» и «Shah Akmal» один человек, а не расхождение.
        """
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        await self._reply(update, "⏳ сверяю таблицу и базу…")
        from db import mirror

        try:
            info = await asyncio.to_thread(
                mirror.compare, self.sheets, self.config.DRIVERS_PASSENGERS_SHEET
            )
        except Exception as e:
            logger.exception("diff failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if not info.get("enabled"):
            await self._reply(
                update, "⚪️ БД выключена: переменная DATABASE_URL не задана.",
                reply_markup=self.kb_main(uid),
            )
            return

        if info.get("no_data_for_day"):
            await self._reply(
                update,
                f"🔍 Сверка на {info['day']}\n\n"
                f"За этот день в базе нет ни одной связи — снимка ещё не "
                f"было, и состав никто не менял.\n\n"
                f"Сравнивать не с чем. Сделай /db_capture и повтори.",
                reply_markup=self.kb_main(uid),
            )
            return

        всего = (len(info["only_sheet"]) + len(info["only_db"])
                 + len(info["differs"]))
        lines = [f"🔍 Сверка на {info['day']}",
                 f"Водителей: в таблице {info['sheet_drivers']}, "
                 f"в базе {info['db_drivers']}", ""]
        if not всего:
            lines.append("✅ Расхождений нет — картины совпадают.")
        else:
            lines.append(f"Расхождений: {всего}")
            if info["only_sheet"]:
                lines.append(f"\n• Есть в таблице, нет в базе: {len(info['only_sheet'])}")
                for tg, names in info["only_sheet"][:5]:
                    lines.append(f"    {tg}: {', '.join(names)}")
            if info["only_db"]:
                lines.append(f"\n• Есть в базе, нет в таблице: {len(info['only_db'])}")
                for tg, names in info["only_db"][:5]:
                    lines.append(f"    {tg}: {', '.join(names)}")
            if info["differs"]:
                lines.append(f"\n• Разный состав: {len(info['differs'])}")
                for tg, s_names, d_names in info["differs"][:5]:
                    lines.append(f"    {tg}: таблица [{', '.join(s_names)}] "
                                 f"≠ база [{', '.join(d_names)}]")
            lines.append("\nℹ️ Связи за день берутся из изменений состава "
                         "(зеркало) или из снимка. Выровнять: /db_capture — "
                         "он снимет состояние и пересоберёт связи за сегодня.")
        await self._reply(update, "\n".join(lines)[:4000],
                          reply_markup=self.kb_main(uid))

    async def db_restore_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Вернуть списки, стёртые еженедельной проверкой. /db_restore [дней] [да]

        Без слова «да» только показывает, что будет сделано: очистка
        затрагивает живых людей, и подтверждение здесь дешевле отката.
        """
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        args = [a.casefold() for a in (context.args or [])]
        days = int(args[0]) if args and args[0].isdigit() else 7
        apply = "да" in args or "yes" in args

        from db import store

        try:
            events = await asyncio.to_thread(store.cleared_carpools, days)
        except Exception as e:
            logger.exception("restore failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if not events:
            await self._reply(
                update,
                f"Журнал пуст за {days} дн. — очисток не было либо они "
                "случились до того, как журнал начали вести.",
                reply_markup=self.kb_main(uid),
            )
            return

        # Берём последнюю очистку на каждого водителя: если список стирали
        # дважды, вернуть надо то, что было перед первой потерей смысла нет —
        # актуальнее последнее известное состояние.
        latest: dict[int, tuple] = {}
        for happened_at, subject, details in events:
            tg = details.get("telegram_id")
            if tg is not None and tg not in latest:
                latest[tg] = (happened_at, subject, details.get("passengers") or [])

        plan, skipped = [], []
        for tg, (when, name, passengers) in latest.items():
            if not passengers:
                continue
            try:
                dp = await asyncio.to_thread(self.sheets.get_driver_passengers, tg)
            except Exception:
                skipped.append((name, "не удалось прочитать запись"))
                continue
            if dp is None:
                skipped.append((name, "водителя больше нет"))
            elif dp.passengers:
                # Не затираем: водитель уже собрал список заново, и он
                # свежее того, что лежит в журнале.
                skipped.append((name, "список уже не пуст"))
            else:
                plan.append((tg, name, passengers))

        if not apply:
            lines = [f"🔎 Что вернётся (за {days} дн.)", "",
                     f"Водителей: {len(plan)}, пассажиров: "
                     f"{sum(len(p) for _, _, p in plan)}", ""]
            for _, name, passengers in plan[:12]:
                lines.append(f"• {name}: {', '.join(passengers)}")
            if len(plan) > 12:
                lines.append(f"…ещё {len(plan) - 12}")
            if skipped:
                lines.append(f"\nПропущено: {len(skipped)}")
                for name, why in skipped[:5]:
                    lines.append(f"  • {name} — {why}")
            lines.append("\nЧтобы выполнить: /db_restore "
                         f"{days} да")
            await self._reply(update, "\n".join(lines)[:4000],
                              reply_markup=self.kb_main(uid))
            return

        await self._reply(update, f"⏳ возвращаю {len(plan)} списков…")
        done, failed = 0, []
        for tg, name, passengers in plan:
            try:
                await asyncio.to_thread(
                    self.sheets.assign_passengers_to_driver,
                    driver_tgid=tg, driver_name=name, passenger_names=passengers,
                )
                dp = DriverPassengers(driver_name=name, driver_tgid=tg,
                                      passengers=passengers)
                await asyncio.to_thread(self.sheets.upsert_driver_passengers, dp)
                done += 1
            except Exception as e:  # noqa: BLE001
                failed.append((name, str(e)[:60]))

        lines = [f"✅ Возвращено списков: {done}"]
        if failed:
            lines.append(f"❌ Не удалось: {len(failed)}")
            for name, why in failed[:5]:
                lines.append(f"  • {name}: {why}")
        await self._reply(update, "\n".join(lines), reply_markup=self.kb_main(uid))

    async def db_stale_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Водители со списком, но без отметок в табеле. /db_stale [дней]"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        args = context.args or []
        days = int(args[0]) if args and args[0].isdigit() else 7

        from db import svodka

        try:
            info = await asyncio.to_thread(svodka.stale_drivers, days)
        except Exception as e:
            logger.exception("stale failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if not info.get("enabled"):
            await self._reply(
                update, "⚪️ БД выключена: переменная DATABASE_URL не задана.",
                reply_markup=self.kb_main(uid),
            )
            return

        rows = info["rows"]
        if not rows:
            await self._reply(
                update,
                f"✅ Таких нет: у всех со списком есть отметки за последние "
                f"{days} дн. табелей.",
                reply_markup=self.kb_main(uid),
            )
            return

        lines = [f"🧹 Держат пассажиров без отметок в табеле ({days} дн.)",
                 f"Водителей: {len(rows)}, заблокировано людей: {info['blocked']}",
                 "",
                 "⚠️ Считается по ЗАГРУЖЕННЫМ табелям. Если табель недели ещё "
                 "не в таблице, человек попадёт сюда зря — смотри дату справа.",
                 ""]
        for name, tg_id, passengers, last_seen, site, site_last in rows[:15]:
            seen = last_seen.isoformat() if last_seen else "никогда"
            горизонт = (f"табель {site or '?'} загружен по {site_last}"
                        if site_last else "табелей по его объекту нет")
            lines.append(f"• {name} (id {tg_id})")
            lines.append(f"  последняя отметка {seen} · {горизонт}")
            lines.append(f"  держит: {', '.join(passengers)}")
        if len(rows) > 15:
            lines.append(f"\n…ещё {len(rows) - 15}")
        lines.append("\nЧтобы освободить людей — удали водителя или очисти "
                     "его список в таблице.")
        await self._reply(update, "\n".join(lines)[:4000],
                          reply_markup=self.kb_main(uid))

    async def db_report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сводка по доплатам из базы. /db_report 2026-09-28 2026-10-04"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        args = context.args or []
        if len(args) < 2:
            await self._reply(
                update,
                "/db_report ГГГГ-ММ-ДД ГГГГ-ММ-ДД\n\n"
                "Пример: /db_report 2026-09-28 2026-10-04\n\n"
                "По умолчанию считает справедливо: записанные дни плюс "
                "спорные, где ни один пассажир не ехал с другим водителем "
                "и хотя бы двое были на работе.\n\n"
                "Добавь в конец слово:\n"
                "• «строго» — только дни с записью о двух пассажирах;\n"
                "• «табель» — правило GAS, пассажиры раз на период.\n\n"
                "За 28.09–04.10 это 1089, 909 и 1260 дней.",
                reply_markup=self.kb_main(uid),
            )
            return

        from datetime import date as _date

        try:
            start, end = (_date.fromisoformat(a) for a in args[:2])
        except ValueError:
            await self._reply(
                update, "Даты в виде ГГГГ-ММ-ДД, например 2026-09-28.",
                reply_markup=self.kb_main(uid),
            )
            return
        if end < start:
            start, end = end, start
        хвост = " ".join(args).casefold()
        mode = ("timesheet" if "табель" in хвост
                else "strict" if "строго" in хвост else "fair")

        await self._reply(update, f"⏳ считаю сводку {start} — {end}…")

        from db import svodka

        try:
            info = await asyncio.to_thread(
                lambda: svodka.export(self.sheets, start, end, mode=mode)
            )
        except Exception as e:
            logger.exception("svodka failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if not info.get("enabled"):
            await self._reply(
                update, "⚪️ БД выключена: переменная DATABASE_URL не задана.",
                reply_markup=self.kb_main(uid),
            )
            return

        rule = {
            "fair": "справедливое: записанные дни плюс спорные, "
                    "где пассажиры были свободны и на работе",
            "strict": "строгое: только дни с записью о двух пассажирах",
            "timesheet": "по табелю (как GAS): пассажиры раз на период",
        }[info.get("mode", "fair")]
        lines = [f"📊 Сводка {start} — {end}",
                 f"Правило — {rule}",
                 f"Недель в периоде: {len(info['weeks'])}", ""]
        if not info["written"]:
            lines.append("Ни одного засчитанного дня за период.")
        for title, count in sorted(info["written"].items()):
            lines.append(f"• {title}: {count} водителей")

        # Водители с пассажирами, но без отметки в табеле: для них это
        # потерянные деньги, и причина должна быть названа, а не скрыта.
        unmarked = info.get("unmarked") or []
        if unmarked:
            lines.append(f"\n⚠️ Есть пассажиры, но нет отметки в табеле: "
                         f"{len(unmarked)} чел.")
            for name, days in unmarked[:8]:
                lines.append(f"  • {name}: {days} дн.")
            if len(unmarked) > 8:
                lines.append(f"  …ещё {len(unmarked) - 8}")

        await self._reply(update, "\n".join(lines)[:4000],
                          reply_markup=self.kb_main(uid))

    async def db_site_rename_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Переименовать объект, сохранив историю. /db_site_rename AMAZON TULANE [Имя]"""
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        args = context.args or []
        if len(args) < 2:
            await self._reply(
                update,
                "/db_site_rename СТАРЫЙ НОВЫЙ [Отображаемое имя]\n\n"
                "Пример: /db_site_rename AMAZON TULANE Tulane\n\n"
                "История переезжает на новый объект, старое имя становится "
                "псевдонимом — исторические листы продолжат читаться.",
                reply_markup=self.kb_main(uid),
            )
            return

        from db import roster

        try:
            info = await asyncio.to_thread(
                roster.rename_site, args[0], args[1],
                " ".join(args[2:]) or None,
            )
        except Exception as e:
            logger.exception("rename_site failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if not info.get("ok"):
            reasons = {"same": "Имена совпадают — переименовывать нечего.",
                       "no_old": f"Объекта «{args[0]}» в справочнике нет."}
            await self._reply(
                update, reasons.get(info.get("reason"), "Не удалось."),
                reply_markup=self.kb_main(uid),
            )
            return

        lines = [f"✅ {info['old']} → {info['new']}", "",
                 f"Перенесено отметок: {info['presence_moved']}",
                 f"Людей переведено: {info['people']}"]
        if info["presence_dropped"]:
            lines.append(f"⚠️ Не перенеслось: {info['presence_dropped']} "
                         "(человек был отмечен в этот день на обоих объектах)")
        lines.append("")
        lines.append("Старое имя осталось псевдонимом — исторические листы читаются.")
        await self._reply(update, "\n".join(lines), reply_markup=self.kb_main(uid))

    async def db_sheets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Все листы таблицы с разбором. /db_sheets

        Отвечает на вопрос, который иначе решается только глазами: какие
        листы есть и почему часть из них не попадает в импорт. Диагностика
        внутри /db_import показывает лишь «похожие на табель» — лист без
        признаков объекта и без дат в неё не попадает вовсе.
        """
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        from db.importer import is_timesheet, site_from_sheet_name, week_dates_from_name

        try:
            titles = await asyncio.to_thread(self.sheets.sheet_titles)
        except Exception as e:
            logger.exception("sheet_titles failed")
            await self._reply(
                update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid)
            )
            return

        timesheets, others = [], []
        for title in titles:
            if is_timesheet(title):
                dates = week_dates_from_name(title)
                timesheets.append(f"  ✅ {title} → {dates[0]}")
            else:
                site = site_from_sheet_name(title)
                dates = week_dates_from_name(title)
                mark = []
                if site:
                    mark.append(f"объект {site}")
                if dates:
                    mark.append(f"недели с {dates[0]}")
                others.append(f"  — {title}" + (f"  ({', '.join(mark)})" if mark else ""))

        lines = [f"📄 Листов всего: {len(titles)}", "",
                 f"Табели ({len(timesheets)}):"] + sorted(timesheets)
        lines += ["", f"Остальные ({len(others)}):"] + others
        text = "\n".join(lines)
        await self._reply(update, text[:4000], reply_markup=self.kb_main(uid))

    async def db_merge_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Склеить две записи одного человека. /db_merge Старое Имя | Новое Имя

        Разделитель — вертикальная черта: в именах есть пробелы, по ним
        разбить нельзя.
        """
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        raw = " ".join(context.args or [])
        if "|" not in raw:
            await self._reply(
                update,
                "Укажи оба имени через вертикальную черту:\n"
                "/db_merge Старое Имя | Новое Имя\n\n"
                "История старой записи перейдёт к новой, старая будет удалена.",
                reply_markup=self.kb_main(uid),
            )
            return

        old_name, new_name = (part.strip() for part in raw.split("|", 1))

        from db import roster

        try:
            info = await asyncio.to_thread(roster.merge_people, old_name, new_name)
        except Exception as e:
            logger.exception("merge failed")
            await self._reply(
                update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid)
            )
            return

        if not info.get("ok"):
            reasons = {
                "same": "Это одно и то же имя — склеивать нечего.",
                "empty": "Одно из имён пустое.",
                "not_found": (
                    f"Не нашёл записи. Старое «{old_name}»: "
                    f"{'есть' if info.get('old_found') else 'НЕТ'}, "
                    f"новое «{new_name}»: "
                    f"{'есть' if info.get('new_found') else 'НЕТ'}.\n\n"
                    "Если новой записи нет — сначала запусти /db_import."
                ),
            }
            await self._reply(
                update,
                reasons.get(info.get("reason"), "Не удалось склеить."),
                reply_markup=self.kb_main(uid),
            )
            return

        moved, dropped = info["moved"], info["dropped"]
        lines = [
            "✅ Склеено", "",
            f"«{info['old']}» → «{info['new']}»", "",
            f"Отметок в табеле: {moved['presence']}",
            f"Поездок как водитель: {moved['as_driver']}",
            f"Поездок как пассажир: {moved['as_passenger']}",
        ]
        # Не перенеслось то, что нарушило бы ограничение: человек уже отмечен
        # в этот день или уже едет. Молчать об этом нельзя — это потеря истории.
        lost = {k: v for k, v in dropped.items() if v}
        if lost:
            lines.append("")
            lines.append("⚠️ Не перенеслось (записи уже были у нового имени):")
            names = {"presence": "отметок", "as_driver": "поездок водителем",
                     "as_passenger": "поездок пассажиром"}
            for key, count in lost.items():
                lines.append(f"  • {names[key]}: {count}")
        lines.append("")
        lines.append("Старая запись удалена.")
        await self._reply(update, "\n".join(lines), reply_markup=self.kb_main(uid))

    async def db_export_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выгрузка из БД обратно в таблицу. /db_export [лист]

        Пишет только в листы «_db_*» — их не читает ни бот, ни GAS, поэтому
        второго писателя ни у одной существующей сущности не появляется.
        """
        uid = update.effective_user.id
        if uid not in self.config.ADMIN_USER_IDS:
            return

        args = context.args or []
        only = args[0].strip() if args else None
        await self._reply(update, "⏳ выгружаю в таблицу…")

        from db import export as db_export

        try:
            info = await asyncio.to_thread(db_export.export, self.sheets, only)
        except Exception as e:
            logger.exception("export failed")
            await self._reply(
                update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid)
            )
            return

        if not info.get("enabled"):
            await self._reply(
                update,
                "⚪️ БД выключена: переменная DATABASE_URL не задана.",
                reply_markup=self.kb_main(uid),
            )
            return

        sheets_written = info["sheets"]
        if not sheets_written:
            await self._reply(
                update,
                "Ни одна выгрузка не подошла под фильтр.\n"
                "Доступны: " + ", ".join(e.title for e in db_export.EXPORTS),
                reply_markup=self.kb_main(uid),
            )
            return

        lines = ["✅ Выгружено в таблицу", ""]
        for title, count in sheets_written.items():
            lines.append(f"• {title}: {count} строк")
        lines.append(
            "\nЛисты перезаписываются целиком при каждой выгрузке — "
            "править их смысла нет, правки затрутся."
        )
        await self._reply(update, "\n".join(lines), reply_markup=self.kb_main(uid))

    async def _run_capture(self, update, uid, work, label: str):
        """Общая обвязка для команд захвата: прогресс, запуск, отчёт."""
        await self._reply(update, f"⏳ {label}…")
        try:
            info = await asyncio.to_thread(work)
        except Exception as e:
            logger.exception("capture failed")
            await self._reply(update, f"❌ Ошибка: {e}", reply_markup=self.kb_main(uid))
            return

        if not info.get("enabled"):
            await self._reply(
                update,
                "⚪️ Захват выключен: переменная DATABASE_URL не задана.",
                reply_markup=self.kb_main(uid),
            )
            return

        lines = [
            f"✅ {label} — готово",
            "",
            f"Прочитано строк: {info['read']}",
            f"Записано новых: {info['inserted']}",
            f"Обновлено: {info['updated']}",
        ]
        if info["collapsed"]:
            lines.append(f"Схлопнуто дублей: {info['collapsed']}")
        links = info.get("links") or {}
        if links.get("written") is not None:
            lines.append(f"Связей пересобрано: {links['written']}")
        elif links.get("error"):
            lines.append(f"⚠️ Связи не пересобраны: {links['error'][:80]}")
        if info["skipped"]:
            lines.append(f"Пропущено: {info['skipped']}")
            for reason, count in sorted(info["reasons"].items()):
                lines.append(f"  • {reason}: {count}")
        await self._reply(update, "\n".join(lines), reply_markup=self.kb_main(uid))

    async def db_roster_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Ежедневная синхронизация ростера.

        Без неё база обречена отставать: HR меняет смены и добавляет людей
        каждый день, а импорт запускали руками. Сверка 08.10 показала
        50 расхождений из 1517 — все от устаревшего ростера, и именно они
        делали переход на чтение из БД небезопасным.

        Идёт до вечернего снимка, чтобы снимок уже ложился на свежие имена.
        """
        from db import roster, store

        if not store.enabled():
            return
        try:
            info = await asyncio.to_thread(
                lambda: roster.import_roster(
                    self.sheets, drivers_sheet=self.config.DRIVERS_SHEET,
                    roster_only=True)
            )
            logger.info("roster sync: %s", info)
            if self.config.ADMIN_CHAT_ID and info.get("enabled"):
                await context.bot.send_message(
                    chat_id=self.config.ADMIN_CHAT_ID,
                    text=(f"👥 Ростер синхронизирован\n"
                          f"Людей: {info['people_total']} (+{info['people_new']})"),
                )
        except Exception as e:
            logger.exception("roster sync failed")
            if self.config.ADMIN_CHAT_ID:
                await context.bot.send_message(
                    chat_id=self.config.ADMIN_CHAT_ID,
                    text=f"⚠️ Синхронизация ростера не удалась: {str(e)[:200]}",
                )

    async def db_capture_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Ежедневный захват по расписанию. Параллелен снапшоту GAS."""
        from db import store

        if not store.enabled():
            return
        try:
            info = await asyncio.to_thread(
                store.capture_live, self.sheets, self.config.DRIVERS_PASSENGERS_SHEET
            )
            logger.info("daily capture: %s", info)
            if self.config.ADMIN_CHAT_ID:
                await context.bot.send_message(
                    chat_id=self.config.ADMIN_CHAT_ID,
                    text=(
                        f"🗄 Снапшот записан\n"
                        f"Прочитано {info['read']}, новых {info['inserted']}, "
                        f"обновлено {info['updated']}, пропущено {info['skipped']}"
                    ),
                )
        except Exception as e:
            logger.exception("daily capture failed")
            if self.config.ADMIN_CHAT_ID:
                await context.bot.send_message(
                    chat_id=self.config.ADMIN_CHAT_ID,
                    text=f"🗄 ❌ Снапшот НЕ записан: {str(e)[:500]}",
                )

    # ======================================================
    # Помощь и роль
    # ======================================================

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Инструкция под роль. /help или кнопка «❓ Помощь»."""
        uid = update.effective_user.id
        is_driver = self.known_role(uid)
        if is_driver is None:
            try:
                is_driver = self.sheets.get_driver(uid) is not None
                self.remember_role(uid, is_driver)
            except Exception:
                is_driver = False

        body = t("help.driver" if is_driver else "help.passenger", tg_id=uid)
        text = "\n\n".join([
            t("help.header", tg_id=uid),
            body,
            t("help.footer", tg_id=uid),
        ])
        await self._reply(update, text, reply_markup=self.kb_main(uid, is_driver))

    # ======================================================
    # Пассажир открепляется сам
    #
    # Раньше «пассажир уже записан к другому водителю» был тупиком: снять
    # человека мог только администратор вручную. Теперь пассажир делает это
    # сам, а водитель получает уведомление — ошибка видна сразу и
    # исправляется кнопкой «Добавить пассажиров».
    # ======================================================

    async def leave_carpool_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        await self._reply(update, t("leave.ask_name", tg_id=uid))
        return ST_LEAVE_NAME

    async def leave_carpool_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        raw = (update.effective_message.text or "").strip()

        emp = await asyncio.to_thread(self.sheets.get_employee_by_name, raw)
        if emp is None or not emp.name:
            await self._reply(update, t("leave.not_employee", tg_id=uid),
                              reply_markup=self.kb_main(uid))
            return ConversationHandler.END

        found = await asyncio.to_thread(self.sheets.find_driver_for_passenger, emp.name)
        if not found:
            await self._reply(update, t("leave.not_in_carpool", tg_id=uid),
                              reply_markup=self.kb_main(uid))
            return ConversationHandler.END

        driver_tgid, driver_name = found
        context.user_data["leave_name"] = emp.name
        context.user_data["leave_driver_tgid"] = driver_tgid
        context.user_data["leave_driver_name"] = driver_name

        await self._reply(
            update,
            t("leave.confirm", tg_id=uid, driver=driver_name),
            reply_markup=self.kb_yes_no(uid),
        )
        return ST_LEAVE_CONFIRM

    async def leave_carpool_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        name = context.user_data.get("leave_name")
        driver_tgid = context.user_data.get("leave_driver_tgid")
        driver_name = context.user_data.get("leave_driver_name", "")
        if not name:
            await self._reply(update, t("leave.error", tg_id=uid), reply_markup=self.kb_main(uid))
            return ConversationHandler.END

        intent = parse_yes_no_intent(update.effective_message.text or "")
        if intent == "unclear":
            await self._reply(
                update,
                t("leave.confirm", tg_id=uid, driver=driver_name),
                reply_markup=self.kb_yes_no(uid),
            )
            return ST_LEAVE_CONFIRM
        if intent == "no":
            context.user_data.clear()
            await self._reply(update, t("leave.cancelled", tg_id=uid, driver=driver_name),
                              reply_markup=self.kb_main(uid))
            return ConversationHandler.END

        try:
            # except_tgid=0 — исключать некого, снимаем у текущего водителя.
            await asyncio.to_thread(
                self.sheets.unlink_passenger_everywhere, name, except_tgid=0
            )
        except Exception as e:
            logger.exception("leave_carpool failed")
            await self._reply(update, t("leave.error", tg_id=uid),
                              reply_markup=self.kb_main(uid))
            await self.log_admin(context, "Leave carpool FAILED",
                                 f"{name} -> {driver_name}: {e}", update)
            return ConversationHandler.END

        await self._reply(update, t("leave.done", tg_id=uid, driver=driver_name),
                          reply_markup=self.kb_main(uid))

        # Водитель должен узнать сразу: его карпул мог упасть ниже двух
        # человек, а это потеря дня в доплатах.
        if driver_tgid:
            try:
                await context.bot.send_message(
                    chat_id=driver_tgid,
                    text=t("leave.driver_notice", tg_id=driver_tgid,
                           passenger=name, button=button("btn.add_passengers", driver_tgid)),
                )
            except Exception:
                logger.info("не удалось уведомить водителя %s", driver_tgid)

        await self.log_admin(context, "Passenger left carpool",
                             f"{name} открепился от {driver_name} (tg_id={driver_tgid})", update)
        context.user_data.clear()
        return ConversationHandler.END
