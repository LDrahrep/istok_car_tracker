from locales import ru, en, es, tg, uz

LOCALES = {
    "ru": ru.STRINGS,
    "en": en.STRINGS,
    "es": es.STRINGS,
    "tg": tg.STRINGS,
    "uz": uz.STRINGS,
}

# Название языка на нём самом: выбирает тот, кто пока читает только на своём.
LANGUAGE_NAMES = {
    "ru": "Русский",
    "en": "English",
    "es": "Español",
    "tg": "Тоҷикӣ",
    "uz": "Oʻzbekcha",
}

DEFAULT_LANG = "ru"

# Ввод сравнивается с табелем и таблицами, а там латиница: кнопки могут быть
# на любом языке, но вводить всё равно по-английски. Подсказка живёт в одном
# ключе и дописывается ко всему, что просит ввод, и к ошибкам «не нашёл».
_ENDS_WITH_ENGLISH_HINT = (
    "driver.enter_name", "driver.enter_car", "driver.enter_plates",
    "driver.ask_city", "passengers.enter", "leave.ask_name",
    "search.ask_name", "search.ask_city",
    "driver.name_not_in_employees", "driver.name_suggestions",
    "leave.not_employee", "search.not_employee",
    "driver.city_not_found", "search.city_not_found",
    "lang.switched", "help.footer",
)
for _strings in LOCALES.values():
    for _key in _ENDS_WITH_ENGLISH_HINT:
        _strings[_key] += "\n\n" + _strings["hint.english"]
