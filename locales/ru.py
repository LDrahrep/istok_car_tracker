STRINGS = {
    # ===== Кнопки =====
    "btn.become_driver": "🚗 Стать водителем",
    "btn.add_passengers": "👥 Добавить пассажиров",
    "btn.remove_passenger": "🧑‍🤝‍🧑 Удалить пассажира",
    "btn.my_record": "📋 Моя запись",
    "btn.stop_being_driver": "🛑 Перестать быть водителем",
    "btn.cancel": "↩️ Назад / Отмена",
    "btn.yes": "✅ Да",
    "btn.no": "❌ Нет",
    "btn.admin_weekly_target": "🎯 Проверка пассажиров (точечно)",
    "btn.admin_mode_tgid": "👤 По Telegram ID",
    "btn.admin_mode_shift": "🕒 По смене сотрудников",
    "btn.shift_day": "☀️ Дневная смена",
    "btn.shift_night": "🌙 Ночная смена",
    "btn.shift_meltech_day": "🔧☀️ Meltech Day",
    "btn.shift_meltech_night": "🔧🌙 Meltech Night",

    # ===== Start / общее =====
    "start.greeting": "Привет! Выбери действие кнопкой ниже.",
    "cancel.done": "Ок, отменено.",

    # ===== Стать водителем =====
    "driver.enter_name": "Напиши имя и фамилию.\nПример: Ivan Ivanov",
    "driver.enter_car": "Напиши модель машины.\nПример: Toyota Camry",
    "driver.enter_plates": "Напиши номер машины.\nПример: ABC 1234",
    "driver.already_registered": "Ты уже зарегистрирован как водитель.\nЕсли хочешь обновить данные — сначала нажми «🛑 Перестать быть водителем».",
    "driver.already_passenger": "⛔ Ты уже записан как пассажир у водителя {driver}.\nПопроси его убрать тебя из списка, потом возвращайся.",
    "driver.name_not_in_employees": "⛔ Такого сотрудника не нашёл.\nПроверь написание имени, или обратись к администратору.",
    "driver.name_suggestions": "⛔ Такого сотрудника не нашёл.\nВозможно, ты имел в виду:\n{suggestions}",
    "driver.shift_unknown": "⛔ У тебя не указана смена.\nОбратись к администратору.",
    "driver.registered": "✅ Готово! Ты зарегистрирован как водитель.\nТеперь можешь добавлять пассажиров.",
    "driver.register_error": "❌ Ошибка при регистрации. Попробуй ещё раз.",

    # ===== Stop being driver =====
    "stop_driver.confirm": "Точно перестать быть водителем?\nВсе твои пассажиры станут свободными.",
    "stop_driver.done": "✅ Готово! Ты больше не водитель.\nТеперь тебя можно добавить пассажиром 😉",
    "stop_driver.nothing": "Ок, ничего не меняю.",
    "stop_driver.error": "❌ Ошибка при удалении. Попробуй ещё раз.",
    "stop_driver.not_a_driver": "Ты не зарегистрирован как водитель.",

    # ===== Add passengers =====
    "passengers.enter": "Введи пассажиров (каждого с новой строки), максимум 4.\n\nПример:\nIvan Ivanov\nMaria Ivanova",
    "passengers.not_a_driver": "⛔ Ты не зарегистрирован как водитель.\nСначала нажми «🚗 Стать водителем».",
    "passengers.added": "✅ Добавлены: {names}",
    "passengers.nothing_added": "⚠️ Никого не добавил.",
    "passengers.max_reached": "⚠️ У тебя уже {count} пассажиров (максимум 4). Удали кого-то, потом добавляй новых.",
    "passengers.error": "❌ Ошибка при добавлении. Попробуй ещё раз.",

    # Пассажир-специфичные предупреждения
    "passenger_warning.not_found": "• {name}: сотрудника ещё не добавили.",
    "passenger_warning.not_found_suggest": "• {name}: сотрудника ещё не добавили. Возможно, ты имел в виду: {suggestions}",
    "passenger_warning.wrong_shift": "• {name}: сотрудник в другой смене.",
    "passenger_warning.already_with_driver": "• {name}: уже ездит с водителем {driver}.",
    "passenger_warning.self": "🙃 Водитель не может быть пассажиром — этот пункт пропущен.\nЕсли ты больше не водитель, нажми «🛑 Перестать быть водителем», и тогда тебя смогут добавить пассажиром.",

    # ===== Remove passenger =====
    "remove_passenger.choose": "Выбери кого убрать:",
    "remove_passenger.no_passengers": "У тебя нет пассажиров.",
    "remove_passenger.done": "✅ Пассажир {name} убран.",
    "remove_passenger.not_found": "⚠️ Пассажир {name} не найден.",
    "remove_passenger.error": "❌ Ошибка при удалении. Попробуй ещё раз.",
    "remove_passenger.not_a_driver": "⛔ Ты не зарегистрирован как водитель.",

    # ===== My record =====
    "my_record.empty": "У тебя нет записи. Нажми «🚗 Стать водителем».",
    "my_record.text": "📋 Твоя запись:\n\n👤 Имя: {name}\n🚗 Машина: {car}\n🔢 Номер: {plates}\n📅 Смена: {shift}\n\n👥 Пассажиры ({count}):\n{passengers}",
    "my_record.no_passengers": "— нет",

    # ===== Weekly check =====
    "weekly.greeting": "📅 Еженедельная проверка списка пассажиров\n\nТекущие пассажиры:\n{passengers}\n\nВсё актуально?",
    "weekly.no_passengers": "Нет пассажиров",
    "weekly.yes_answer": "Ок, список оставлен.",
    "weekly.no_answer": "Список очищен.",
    "weekly.error": "❌ Ошибка при очистке. Обратись к администратору.",
    "weekly.expired_deleted": "⏰ Ты не ответил на еженедельную проверку за 2 часа.\nТвоя запись удалена. Чтобы восстановить — нажми «🚗 Стать водителем».",

    # ===== Admin =====
    "admin.not_authorized": "⛔ У тебя нет доступа.",
    "admin.weekly_choose_mode": "Как отправить проверку?",
    "admin.weekly_enter_tgid": "Введи Telegram ID водителя (число).\nПример: 123456789",
    "admin.weekly_tgid_invalid": "Telegram ID должен быть числом.\nПример: 123456789",
    "admin.weekly_driver_not_found": "Водитель с Telegram ID {driver_id} не найден.",
    "admin.weekly_choose_shift": "Выбери смену:",
    "admin.weekly_sent_tgid": "Проверка отправлена водителю (ID: {driver_id}).",
    "admin.weekly_sent_shift": "Проверка отправлена {count} водителям смены {shift}.",
    "admin.weekly_no_drivers": "Нет водителей в этой смене.",
    "admin.broadcast_usage": "Напиши текст после команды.\nПример: /broadcast Завтра обновление смен",
    "admin.broadcast_confirm": "Сообщение:\n\n{text}\n\nОтправить {count} водителям?",
    "admin.broadcast_cancelled": "Рассылка отменена.",
    "admin.broadcast_text_lost": "Текст сообщения не найден. Попробуй ещё раз.",
    "admin.broadcast_result": "✅ Отправлено: {sent} водителям.",
    "admin.broadcast_failed_line": "\n❌ Не доставлено: {failed}",
    "admin.broadcast_keyboard_done": "✅ Клавиатура отправлена: {sent} водителям.",
    "admin.broadcast_keyboard_failed": "\n❌ Не удалось: {failed}",
    "admin.keyboard_update": "🔄 Бот обновлён! Клавиатура обновлена.\nИспользуй кнопки ниже:",
    "admin.report_not_found": "Отчёт не найден. Сначала запусти generateBiWeeklyReport() в GAS.",
    "admin.report_empty": "Отчёт пуст. Сначала запусти generateBiWeeklyReport() в GAS.",
    "admin.report_header": "📊 Сводка: {label_a} | {label_b}\n",

    # ===== Language =====
    "lang.switched_en": "✅ Language set to English. Используй кнопки ниже.",
    "lang.switched_ru": "✅ Язык переключен на русский. Используй кнопки ниже.",

    # ===== Generic =====
    "generic.use_buttons": "Используй кнопки ниже.",
    "generic.contact_admin": "Обратись к администратору.",
}
