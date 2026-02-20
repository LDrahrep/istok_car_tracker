# =========================
# TELEGRAM HANDLERS
# =========================

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

from config import BotConfig, Buttons
from models import Driver, Employee, DriverPassengers, ShiftType, normalize_text, SheetError, ValidationError
from sheets import SheetManager
from persistence import get_state_manager


# Conversation states
ADD_NAME, CONFIRM_PHONE, ADD_SHIFT, ADD_CAR, ADD_PLATES = range(5)
PASS_INPUT = 10
DEL_INPUT = 20


class BotHandlers:
    """All bot handlers"""
    
    def __init__(self, config: BotConfig, sheets: SheetManager):
        self.config = config
        self.sheets = sheets
    
    # =========================
    # KEYBOARD HELPERS
    # =========================
    
    def _main_menu(self, is_admin: bool = False) -> ReplyKeyboardMarkup:
        """Create main menu keyboard"""
        rows = [
            [KeyboardButton(Buttons.ADD)],
            [KeyboardButton(Buttons.PASS)],
            [KeyboardButton(Buttons.DEL)],
            [KeyboardButton(Buttons.MY)],
            [KeyboardButton(Buttons.CANCEL)],
        ]
        if is_admin:
            rows.append([KeyboardButton(Buttons.FORCE_WEEKLY)])
            rows.append([KeyboardButton(Buttons.SHUTDOWN)])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True)
    
    def _yes_no_keyboard(self) -> ReplyKeyboardMarkup:
        """Create yes/no keyboard"""
        return ReplyKeyboardMarkup(
            [[KeyboardButton(Buttons.YES), KeyboardButton(Buttons.NO)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
    
    def _shift_keyboard(self) -> ReplyKeyboardMarkup:
        """Create shift selection keyboard"""
        return ReplyKeyboardMarkup(
            [[KeyboardButton(Buttons.DAY), KeyboardButton(Buttons.NIGHT)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
    
    async def show_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show main menu"""
        is_admin = update.effective_user.id in self.config.ADMIN_USERS
        await update.message.reply_text(
            "Выберите действие кнопками 👇",
            reply_markup=self._main_menu(is_admin),
        )
    
    # =========================
    # BASIC COMMANDS
    # =========================
    
    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        await self.show_menu(update, context)
    
    async def cancel_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle cancel action"""
        context.user_data.clear()
        await update.message.reply_text("Ок, отменил.", reply_markup=ReplyKeyboardRemove())
        await self.show_menu(update, context)
        return ConversationHandler.END
    
    async def my_driver_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current driver info"""
        try:
            driver = self.sheets.get_driver(update.effective_user.id)
            if not driver:
                await update.message.reply_text("Вы не найдены в drivers.")
                await self.show_menu(update, context)
                return
            
            dp = self.sheets.get_driver_passengers(update.effective_user.id)
            passengers = dp.passengers if dp else []
            
            msg = f"🚗 Ваш водитель:\n"
            msg += f"Name: {driver.name}\n"
            msg += f"Shift: {driver.shift.to_display()}\n"
            msg += f"Phone: {driver.phone}\n"
            msg += f"Car: {driver.car}\n"
            msg += f"Plates: {driver.plates}\n\n"
            msg += "👥 Пассажиры:\n"
            msg += "\n".join([f"- {p}" for p in passengers]) if passengers else "- (нет)"
            
            await update.message.reply_text(msg)
            
        except SheetError as e:
            logging.error(f"Error in my_driver_cmd: {e}")
            await update.message.reply_text("⚠️ Ошибка доступа к таблице. Попробуйте позже.")
        
        await self.show_menu(update, context)
    
    async def shutdown_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle shutdown command (admin only)"""
        if update.effective_user.id not in self.config.ADMIN_USERS:
            await update.message.reply_text("Нет доступа.")
            await self.show_menu(update, context)
            return
        
        await update.message.reply_text("Останавливаюсь ✅")
        await context.application.stop()
        await context.application.shutdown()
    
    # =========================
    # ADD DRIVER FLOW
    # =========================
    
    async def add_driver_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start add driver conversation"""
        context.user_data.clear()
        await update.message.reply_text(
            "Введи СВОИ Имя и Фамилию на АНГЛИЙСКОМ ЯЗЫКЕ",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADD_NAME
    
    async def add_driver_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle driver name input"""
        name = update.message.text.strip()
        context.user_data["name"] = name
        
        try:
            employee = self.sheets.get_employee_by_name(name)
            
            if not employee:
                await update.message.reply_text(
                    "Сотрудник не найден в таблице employees.\n"
                    "Обратитесь к менеджеру."
                )
                return ConversationHandler.END
            
            if not employee.phone:
                await update.message.reply_text(
                    "Телефон у сотрудника отсутствует. Обратитесь к менеджеру."
                )
                return ConversationHandler.END
            
            context.user_data["phone"] = employee.phone
            context.user_data["shift_from_employees"] = employee.shift.to_display()
            
            await update.message.reply_text(
                f"Найден номер: {employee.phone}\nЭто правильный номер?",
                reply_markup=self._yes_no_keyboard(),
            )
            return CONFIRM_PHONE
            
        except SheetError as e:
            logging.error(f"Error in add_driver_name: {e}")
            await update.message.reply_text("⚠️ Ошибка доступа к таблице. Попробуйте позже.")
            return ConversationHandler.END
    
    async def confirm_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle phone confirmation"""
        answer = update.message.text.strip().lower()
        
        if answer != "да":
            await update.message.reply_text(
                "Запись не создана. Обратитесь к менеджеру.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await self.show_menu(update, context)
            return ConversationHandler.END
        
        await update.message.reply_text(
            "В какой смене ты работаешь?",
            reply_markup=self._shift_keyboard(),
        )
        return ADD_SHIFT
    
    async def add_driver_shift(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle shift selection"""
        raw = update.message.text.strip()
        shift = ShiftType.from_string(raw)
        
        if shift == ShiftType.UNKNOWN:
            await update.message.reply_text(
                "Пожалуйста, выберите Shift кнопками: Day или Night.",
                reply_markup=self._shift_keyboard(),
            )
            return ADD_SHIFT
        
        context.user_data["shift"] = shift.to_display()
        
        await update.message.reply_text(
            "На какой машине ты ездишь? Напиши:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADD_CAR
    
    async def add_driver_car(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle car input"""
        car = update.message.text.strip()
        if not car:
            await update.message.reply_text("ТЫ НЕ ВПИСАЛ МАШИНУ. Напиши название НА АНГЛИЙСКОМ:")
            return ADD_CAR
        
        context.user_data["car"] = car
        await update.message.reply_text("укажи LICENCE PLATES")
        return ADD_PLATES
    
    async def add_driver_plates(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle license plates input"""
        plates = update.message.text.strip()
        if not plates:
            await update.message.reply_text("ТЫ НЕ ВПИСАЛ LICENCE PLATES, Напиши Еще раз:")
            return ADD_PLATES
        
        try:
            # Create driver object
            driver = Driver(
                name=context.user_data["name"],
                tg_id=update.effective_user.id,
                phone=context.user_data["phone"],
                shift=ShiftType.from_string(context.user_data["shift"]),
                car=context.user_data["car"],
                plates=plates,
                is_active=True,
            )
            
            # Save driver
            is_new, _ = self.sheets.upsert_driver(driver)
            
            # Update employee record (self-assignment)
            result = self.sheets.update_employee_driver(driver.name, driver.name, driver.tg_id)
            if not result.get('success'):
                if result.get('error') == 'sheet_protected':
                    await update.message.reply_text(
                        "⚠️ Не могу обновить данные: таблица защищена от редактирования.\n"
                        "Свяжитесь с администратором для снятия защиты с листа 'employees'."
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Ошибка при обновлении: {result.get('message', 'Unknown error')}"
                    )
                await self.show_menu(update, context)
                return ConversationHandler.END
            
            if is_new:
                await update.message.reply_text("✅ Водитель добавлен")
            else:
                await update.message.reply_text("✅ Водитель обновлён")
            
            logging.info(f"Driver {'created' if is_new else 'updated'}: {driver.name} (TG:{driver.tg_id})")
            
        except SheetError as e:
            logging.error(f"Error in add_driver_plates: {e}")
            await update.message.reply_text("⚠️ Ошибка сохранения. Попробуйте позже.")
        
        await self.show_menu(update, context)
        return ConversationHandler.END
    
    # =========================
    # PASSENGERS FLOW
    # =========================
    
    async def passengers_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start passengers conversation"""
        try:
            driver = self.sheets.get_driver(update.effective_user.id)
            if not driver:
                await update.message.reply_text("Вы не водитель. Сначала добавьте себя.")
                return ConversationHandler.END
            
            await update.message.reply_text(
                f"Напиши имена пассажиров НА АНГЛИЙСКОМ (до {self.config.MAX_PASSENGERS}), "
                f"каждого с новой строки:\n\n"
                "ПРИМЕР:\nIvan Ivanov\nPetr Petrov",
                reply_markup=ReplyKeyboardRemove()
            )
            return PASS_INPUT
            
        except SheetError as e:
            logging.error(f"Error in passengers_start: {e}")
            await update.message.reply_text("⚠️ Ошибка доступа к таблице. Попробуйте позже.")
            return ConversationHandler.END
    
    async def passengers_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle passengers input"""
        try:
            driver = self.sheets.get_driver(update.effective_user.id)
            if not driver:
                await update.message.reply_text("Вы не водитель. Сначала добавьте себя.")
                return ConversationHandler.END
            
            # Parse input
            raw = update.message.text.strip()
            names = [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]
            
            # Remove duplicates while preserving order
            seen = set()
            unique_names = []
            for name in names:
                norm = normalize_text(name)
                if norm not in seen:
                    seen.add(norm)
                    unique_names.append(name)
            
            if not unique_names:
                await update.message.reply_text("Пусто. Введите имена.")
                return PASS_INPUT
            
            if len(unique_names) > self.config.MAX_PASSENGERS:
                await update.message.reply_text(f"Максимум {self.config.MAX_PASSENGERS} пассажира.")
                return PASS_INPUT
            
            # Validate passengers
            valid_employees, errors = self.sheets.validate_passengers(
                driver.tg_id,
                driver.shift,
                unique_names
            )
            
            if errors:
                await update.message.reply_text("\n\n".join(errors))
                await update.message.reply_text("Попробуй снова")
                return PASS_INPUT
            
            # Get existing passengers
            existing_dp = self.sheets.get_driver_passengers(driver.tg_id)
            existing_passengers = existing_dp.passengers if existing_dp else []
            
            # Merge: existing + new (no duplicates)
            existing_norm = {normalize_text(p) for p in existing_passengers}
            merged = list(existing_passengers)
            for name in unique_names:
                if normalize_text(name) not in existing_norm:
                    merged.append(name)
                    existing_norm.add(normalize_text(name))
            
            if len(merged) > self.config.MAX_PASSENGERS:
                await update.message.reply_text(f"Максимум {self.config.MAX_PASSENGERS} пассажира.")
                return PASS_INPUT
            
            # Save to drivers_passengers
            dp = DriverPassengers(
                driver_name=driver.name,
                driver_tgid=driver.tg_id,
                phone=driver.phone,
                shift=driver.shift,
                passengers=merged,
            )
            self.sheets.upsert_driver_passengers(dp)
            
            # Update employees table
            for name in unique_names:
                result = self.sheets.update_employee_driver(name, driver.name, driver.tg_id)
                if not result.get('success'):
                    if result.get('error') == 'sheet_protected':
                        await update.message.reply_text(
                            "⚠️ Не могу обновить данные: таблица защищена от редактирования.\n"
                            "Свяжитесь с администратором для снятия защиты с листа 'employees'."
                        )
                    else:
                        await update.message.reply_text(
                            f"❌ Ошибка при обновлении: {result.get('message', 'Unknown error')}"
                        )
                    await self.show_menu(update, context)
                    return ConversationHandler.END
            
            # Driver self-assignment
            result = self.sheets.update_employee_driver(driver.name, driver.name, driver.tg_id)
            if not result.get('success'):
                if result.get('error') == 'sheet_protected':
                    await update.message.reply_text(
                        "⚠️ Не могу обновить данные: таблица защищена от редактирования.\n"
                        "Свяжитесь с администратором для снятия защиты с листа 'employees'."
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Ошибка при обновлении: {result.get('message', 'Unknown error')}"
                    )
                await self.show_menu(update, context)
                return ConversationHandler.END
            
            await update.message.reply_text("✅ Пассажиры добавлены.")
            logging.info(f"Passengers updated for driver {driver.name} (TG:{driver.tg_id}): {merged}")
            
        except SheetError as e:
            logging.error(f"Error in passengers_input: {e}")
            await update.message.reply_text("⚠️ Ошибка сохранения. Попробуйте позже.")
        
        await self.show_menu(update, context)
        return ConversationHandler.END
    
    # =========================
    # DELETE PASSENGER FLOW
    # =========================
    
    async def delete_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start delete passenger conversation"""
        try:
            driver = self.sheets.get_driver(update.effective_user.id)
            if not driver:
                await update.message.reply_text("Вы не водитель. Сначала добавьте себя.")
                return ConversationHandler.END
            
            dp = self.sheets.get_driver_passengers(update.effective_user.id)
            
            if not dp or not dp.passengers:
                await update.message.reply_text("У вас нет пассажиров для удаления.")
                return ConversationHandler.END
            
            context.user_data["passengers"] = dp.passengers
            
            await update.message.reply_text(
                "Ваши пассажиры:\n" +
                "\n".join([f"- {p}" for p in dp.passengers]) +
                "\n\nВведите имя пассажира для удаления:",
                reply_markup=ReplyKeyboardRemove()
            )
            return DEL_INPUT
            
        except SheetError as e:
            logging.error(f"Error in delete_start: {e}")
            await update.message.reply_text("⚠️ Ошибка доступа к таблице. Попробуйте позже.")
            return ConversationHandler.END
    
    async def delete_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle delete passenger input"""
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("Пусто. Введите имя пассажира:")
            return DEL_INPUT
        
        passengers = context.user_data.get("passengers", [])
        if not passengers:
            await update.message.reply_text("Диалог сбился. Нажмите кнопку «Удалить пассажира» ещё раз.")
            return ConversationHandler.END
        
        # Check if name is in the list
        name_norm = normalize_text(name)
        if name_norm not in {normalize_text(p) for p in passengers}:
            await update.message.reply_text(
                "Пассажир не найден в вашем списке. Введите точное имя ещё раз."
            )
            return DEL_INPUT
        
        try:
            # Remove passenger
            removed = self.sheets.remove_passenger(update.effective_user.id, name)
            
            if not removed:
                await update.message.reply_text("Не смог найти пассажира. Попробуйте ещё раз.")
                return DEL_INPUT
            
            # Clear employee assignment (only if assigned to this driver)
            self.sheets.clear_employee_driver(name, only_if_driver_tgid=update.effective_user.id)
            
            await update.message.reply_text("✅ Пассажир удалён.")
            logging.info(f"Passenger {name} removed from driver TG:{update.effective_user.id}")
            
        except SheetError as e:
            logging.error(f"Error in delete_input: {e}")
            await update.message.reply_text("⚠️ Ошибка удаления. Попробуйте позже.")
        
        await self.show_menu(update, context)
        return ConversationHandler.END
    
    # =========================
    # WEEKLY CHECK
    # =========================
    
    async def weekly_check(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Weekly confirmation check for drivers.
        Runs on Sundays, asks drivers if they still have the same passengers.
        """
        shift_kind_str = context.job.data  # "day" or "night"
        shift_kind = ShiftType.from_string(shift_kind_str)
        
        # Guard: only run on Sundays (unless manual)
        now_local = datetime.now(ZoneInfo(self.config.TIMEZONE))
        is_manual = getattr(context.job, "name", None) == "manual"
        
        if now_local.weekday() != 6 and not is_manual:
            logging.info(
                f"Skipping weekly check: not Sunday. now={now_local.isoformat()} "
                f"tz={self.config.TIMEZONE} shift={shift_kind_str}"
            )
            return
        
        try:
            drivers = self.sheets.get_drivers_for_shift(shift_kind)
            state = get_state_manager()
            
            for driver in drivers:
                if not driver.tg_id:
                    continue
                
                dp = self.sheets.get_driver_passengers(driver.tg_id)
                passengers = dp.passengers if dp else []
                
                txt = "Еженедельная проверка 🚘\n\n"
                txt += "Текущие пассажиры:\n"
                if passengers:
                    txt += "\n".join([f"• {p}" for p in passengers])
                else:
                    txt += "— (пассажиров нет)"
                txt += "\n\nТы всё ещё возишь этих же людей?\nОтветь: Да или Нет\n"
                txt += f"Если не ответишь за {self.config.CONFIRMATION_TIMEOUT_MINUTES} минут — запись будет очищена."
                
                try:
                    await context.bot.send_message(
                        chat_id=driver.tg_id,
                        text=txt,
                        reply_markup=self._yes_no_keyboard()
                    )
                    
                    # Add to pending confirmations
                    state.add_pending_confirmation(driver.tg_id, shift_kind_str)
                    
                    # Schedule timeout
                    context.job_queue.run_once(
                        self.weekly_timeout,
                        when=timedelta(minutes=self.config.CONFIRMATION_TIMEOUT_MINUTES),
                        data={"tg_id": driver.tg_id},
                        name=f"weekly_timeout_{driver.tg_id}",
                    )
                    
                    logging.info(f"Weekly check sent to driver {driver.name} (TG:{driver.tg_id})")
                    
                except Exception as e:
                    logging.error(f"Failed to send weekly check to driver TG:{driver.tg_id}: {e}")
            
            # Update last check timestamp
            state.update_last_weekly_check(shift_kind_str)
            
        except SheetError as e:
            logging.error(f"Error in weekly_check: {e}")
    
    async def weekly_timeout(self, context: ContextTypes.DEFAULT_TYPE):
        """Handle timeout for weekly confirmation"""
        tg_id = context.job.data["tg_id"]
        state = get_state_manager()
        
        # Check if already responded
        if not state.has_pending_confirmation(tg_id):
            return
        
        state.remove_pending_confirmation(tg_id)
        
        try:
            # Clear passengers
            cleared = self.sheets.clear_driver_passengers(tg_id)
            
            # Clear employee assignments
            for passenger_name in cleared:
                self.sheets.clear_employee_driver(passenger_name, only_if_driver_tgid=tg_id)
            
            await context.bot.send_message(
                chat_id=tg_id,
                text=(
                    f"⏰ {self.config.CONFIRMATION_TIMEOUT_MINUTES} минут прошло — "
                    "я очистил запись пассажиров. Если нужно — укажи заново кнопкой «👥 Указать пассажиров»."
                ),
            )
            
            logging.info(f"Weekly timeout cleared passengers for driver TG:{tg_id}")
            
        except Exception as e:
            logging.error(f"Error in weekly_timeout for TG:{tg_id}: {e}")
    
    async def weekly_answer_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle yes/no answer to weekly check"""
        tg_id = update.effective_user.id
        text = update.message.text.strip().lower()
        
        state = get_state_manager()
        
        if not state.has_pending_confirmation(tg_id):
            return  # Not waiting for answer from this user
        
        # Remove from pending
        state.remove_pending_confirmation(tg_id)
        
        # Cancel timeout job
        current_jobs = context.job_queue.get_jobs_by_name(f"weekly_timeout_{tg_id}")
        for job in current_jobs:
            job.schedule_removal()
        
        if text == "да":
            await update.message.reply_text(
                "✅ Ок, ничего не меняю.",
                reply_markup=ReplyKeyboardRemove()
            )
            await self.show_menu(update, context)
            logging.info(f"Weekly check confirmed by driver TG:{tg_id}")
            
        elif text == "нет":
            try:
                # Clear passengers
                cleared = self.sheets.clear_driver_passengers(tg_id)
                
                # Clear employee assignments
                for passenger_name in cleared:
                    self.sheets.clear_employee_driver(passenger_name, only_if_driver_tgid=tg_id)
                
                await update.message.reply_text(
                    "✅ Ок, запись очищена.",
                    reply_markup=ReplyKeyboardRemove()
                )
                await self.show_menu(update, context)
                logging.info(f"Weekly check declined by driver TG:{tg_id}, passengers cleared")
                
            except SheetError as e:
                logging.error(f"Error clearing passengers for TG:{tg_id}: {e}")
                await update.message.reply_text("⚠️ Ошибка очистки. Обратитесь к менеджеру.")
    
    # =========================
    # ADMIN COMMANDS
    # =========================
    
    async def force_weekly_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Force weekly check manually (admin only)"""
        if update.effective_user.id not in self.config.ADMIN_USERS:
            await update.message.reply_text("Нет доступа.")
            return
        
        # Trigger for both shifts
        for shift in ["day", "night"]:
            fake_job = type("Job", (), {"data": shift, "name": "manual"})()
            fake_context = type("Context", (), {
                "job": fake_job,
                "bot": context.bot,
                "job_queue": context.job_queue,
            })()
            
            await self.weekly_check(fake_context)
        
        await update.message.reply_text("✅ Weekly-проверка запущена вручную.")
        await self.show_menu(update, context)
