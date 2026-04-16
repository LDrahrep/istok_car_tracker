STRINGS = {
    # ===== Buttons =====
    "btn.become_driver": "🚗 Become a driver",
    "btn.add_passengers": "👥 Add passengers",
    "btn.remove_passenger": "🧑‍🤝‍🧑 Remove passenger",
    "btn.my_record": "📋 My record",
    "btn.stop_being_driver": "🛑 Stop being a driver",
    "btn.cancel": "↩️ Back / Cancel",
    "btn.yes": "✅ Yes",
    "btn.no": "❌ No",
    "btn.admin_weekly_target": "🎯 Passenger check (targeted)",
    "btn.admin_mode_tgid": "👤 By Telegram ID",
    "btn.admin_mode_shift": "🕒 By shift",
    "btn.shift_day": "☀️ Day shift",
    "btn.shift_night": "🌙 Night shift",
    "btn.shift_meltech_day": "🔧☀️ Meltech Day",
    "btn.shift_meltech_night": "🔧🌙 Meltech Night",

    # ===== Start / common =====
    "start.greeting": "Hi! Choose an action using the button below.",
    "cancel.done": "Ok, cancelled.",

    # ===== Become driver =====
    "driver.enter_name": "Enter your first and last name.\nExample: Ivan Ivanov",
    "driver.enter_car": "Enter your car model.\nExample: Toyota Camry",
    "driver.enter_plates": "Enter your license plate.\nExample: ABC 1234",
    "driver.already_registered": "You're already registered as a driver.\nIf you want to update your data — press \"🛑 Stop being a driver\" first.",
    "driver.already_passenger": "⛔ You are already registered as a passenger of driver {driver}.\nAsk them to remove you from the list, then come back.",
    "driver.name_not_in_employees": "⛔ Employee not found.\nCheck the spelling, or contact the administrator.",
    "driver.name_suggestions": "⛔ Employee not found.\nDid you mean:\n{suggestions}",
    "driver.shift_unknown": "⛔ Your shift is not specified.\nContact the administrator.",
    "driver.registered": "✅ Done! You're registered as a driver.\nYou can now add passengers.",
    "driver.register_error": "❌ Registration error. Please try again.",

    # ===== Stop being driver =====
    "stop_driver.confirm": "Are you sure you want to stop being a driver?\nAll your passengers will become free.",
    "stop_driver.done": "✅ Done! You're no longer a driver.\nNow someone can add you as a passenger 😉",
    "stop_driver.nothing": "Ok, nothing changed.",
    "stop_driver.error": "❌ Error while removing. Please try again.",
    "stop_driver.not_a_driver": "You are not registered as a driver.",

    # ===== Add passengers =====
    "passengers.enter": "Enter passengers (each on a new line), maximum 4.\n\nExample:\nIvan Ivanov\nMaria Ivanova",
    "passengers.not_a_driver": "⛔ You are not registered as a driver.\nFirst press \"🚗 Become a driver\".",
    "passengers.added": "✅ Added: {names}",
    "passengers.nothing_added": "⚠️ No one was added.",
    "passengers.max_reached": "⚠️ You already have {count} passengers (max 4). Remove someone before adding new ones.",
    "passengers.error": "❌ Error while adding. Please try again.",

    # Passenger-specific warnings
    "passenger_warning.not_found": "• {name}: employee not added yet.",
    "passenger_warning.not_found_suggest": "• {name}: employee not added yet. Did you mean: {suggestions}",
    "passenger_warning.wrong_shift": "• {name}: employee is on a different shift.",
    "passenger_warning.already_with_driver": "• {name}: already rides with driver {driver}.",
    "passenger_warning.self": "🙃 A driver cannot be their own passenger — this entry was skipped.\nIf you're no longer a driver, press \"🛑 Stop being a driver\", then someone can add you as a passenger.",

    # ===== Remove passenger =====
    "remove_passenger.choose": "Choose whom to remove:",
    "remove_passenger.no_passengers": "You have no passengers.",
    "remove_passenger.done": "✅ Passenger {name} removed.",
    "remove_passenger.not_found": "⚠️ Passenger {name} not found.",
    "remove_passenger.error": "❌ Error while removing. Please try again.",
    "remove_passenger.not_a_driver": "⛔ You are not registered as a driver.",

    # ===== My record =====
    "my_record.empty": "You don't have a record. Press \"🚗 Become a driver\".",
    "my_record.text": "📋 Your record:\n\n👤 Name: {name}\n🚗 Car: {car}\n🔢 Plates: {plates}\n📅 Shift: {shift}\n\n👥 Passengers ({count}):\n{passengers}",
    "my_record.no_passengers": "— none",

    # ===== Weekly check =====
    "weekly.greeting": "📅 Weekly passenger list check\n\nCurrent passengers:\n{passengers}\n\nEverything correct?",
    "weekly.no_passengers": "No passengers",
    "weekly.yes_answer": "Ok, list kept as is.",
    "weekly.no_answer": "List cleared.",
    "weekly.error": "❌ Error while clearing. Contact the administrator.",
    "weekly.expired_deleted": "⏰ You didn't respond to the weekly check within 2 hours.\nYour record has been deleted. To restore — press \"🚗 Become a driver\".",

    # ===== Admin =====
    "admin.not_authorized": "⛔ You don't have access.",
    "admin.weekly_choose_mode": "How to send the check?",
    "admin.weekly_enter_tgid": "Enter driver's Telegram ID (number).\nExample: 123456789",
    "admin.weekly_tgid_invalid": "Telegram ID must be a number.\nExample: 123456789",
    "admin.weekly_driver_not_found": "Driver with Telegram ID {driver_id} not found.",
    "admin.weekly_choose_shift": "Choose a shift:",
    "admin.weekly_sent_tgid": "Check sent to driver (ID: {driver_id}).",
    "admin.weekly_sent_shift": "Check sent to {count} drivers of shift {shift}.",
    "admin.weekly_no_drivers": "No drivers on this shift.",
    "admin.broadcast_usage": "Write the text after the command.\nExample: /broadcast Shift update tomorrow",
    "admin.broadcast_confirm": "Message:\n\n{text}\n\nSend to {count} drivers?",
    "admin.broadcast_cancelled": "Broadcast cancelled.",
    "admin.broadcast_text_lost": "Message text not found. Please try again.",
    "admin.broadcast_result": "✅ Sent: {sent} drivers.",
    "admin.broadcast_failed_line": "\n❌ Failed: {failed}",
    "admin.broadcast_keyboard_done": "✅ Keyboard sent: {sent} drivers.",
    "admin.broadcast_keyboard_failed": "\n❌ Failed: {failed}",
    "admin.keyboard_update": "🔄 Bot updated! Keyboard refreshed.\nUse the buttons below:",
    "admin.report_not_found": "Report not found. First run generateBiWeeklyReport() in GAS.",
    "admin.report_empty": "Report is empty. First run generateBiWeeklyReport() in GAS.",
    "admin.report_header": "📊 Summary: {label_a} | {label_b}\n",

    # ===== Language =====
    "lang.switched_en": "✅ Language set to English. Use the buttons below.",
    "lang.switched_ru": "✅ Язык переключен на русский. Use the buttons below.",

    # ===== Generic =====
    "generic.use_buttons": "Use the buttons below.",
    "generic.contact_admin": "Contact the administrator.",
}
