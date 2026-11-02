"""Тесты пользовательского слоя: ключи локалей и меню по ролям."""
from __future__ import annotations

import re
from pathlib import Path

from locales import en, es, ru, tg, uz

ROOT = Path(__file__).parent
ALL_LOCALES = (("ru", ru), ("en", en), ("es", es), ("tg", tg), ("uz", uz))
TRANSLATIONS = ALL_LOCALES[1:]  # всё, что сверяется с русским
LOCALE_FILES = tuple(f"locales/{name}.py" for name, _ in ALL_LOCALES)
SOURCES = ["handlers.py", "bot.py", "weekly.py", "report.py", "i18n.py", "sheets.py"]

# Ключи, собираемые динамически (t(key) с переменной) — проверить статикой
# нельзя, перечисляем явно.
DYNAMIC_OK = {"help.driver", "help.passenger",
              "start.role_driver", "start.role_passenger"}


def _used_keys() -> set[str]:
    """Все литеральные ключи из t(...), button(...) и Reason(...).

    Reason обязательно: его код — такой же ключ локали, но опечатка в нём
    не видна ниоткуда. Пользователь получит в чат «[passenger_warning.typo]».
    """
    pattern = re.compile(
        r'\b(?:t|button|Reason)\(\s*["\']([a-z_]+\.[a-z_0-9]+)["\']')
    keys: set[str] = set()
    for name in SOURCES:
        keys |= set(pattern.findall((ROOT / name).read_text(encoding="utf-8")))
    return keys


def test_locales_are_symmetric():
    """Расхождение ключей = англоязычный пользователь увидит русский текст."""
    for name, loc in TRANSLATIONS:
        assert set(ru.STRINGS) == set(loc.STRINGS), (
            f"только в ru: {sorted(set(ru.STRINGS) - set(loc.STRINGS))}, "
            f"только в {name}: {sorted(set(loc.STRINGS) - set(ru.STRINGS))}"
        )


def test_every_used_key_exists():
    """Ловит опечатки и выдуманные имена ключей до того, как их увидит пользователь.

    Без этого t('err.generic') молча вернёт '[err.generic]' прямо в чат.
    """
    missing = sorted(k for k in _used_keys() if k not in ru.STRINGS)
    assert not missing, f"ключи используются, но их нет в локали: {missing}"


def test_latin_locales_have_no_russian():
    for name, loc in (("en", en), ("es", es), ("uz", uz)):
        offenders = [k for k, v in loc.STRINGS.items()
                     if isinstance(v, str) and re.search(r"[а-яё]", v, re.I)]
        assert not offenders, f"русский текст в локали {name}: {offenders}"


def test_translations_are_not_copies_of_english():
    """Ключ есть, симметрия цела, а текст так и остался английским."""
    same_on_purpose = {"btn.no"}  # испанское «❌ No» пишется как английское
    for name, loc in (("es", es), ("tg", tg), ("uz", uz)):
        copied = sorted(k for k in loc.STRINGS
                        if loc.STRINGS[k] == en.STRINGS[k] and k not in same_on_purpose)
        assert not copied, f"не переведено ({name}): {copied}"


def test_tajik_is_not_left_in_russian():
    """Таджикский на кириллице, проверка на русские буквы его не поймает.

    Зато в таджикском алфавите нет ы, щ, ь, ц: они выдают забытый русский текст.
    """
    copied = [k for k in tg.STRINGS if tg.STRINGS[k] == ru.STRINGS[k]]
    assert not copied, f"не переведено (tg): {copied}"
    leftovers = [k for k, v in tg.STRINGS.items() if re.search(r"[ыщьц]", v, re.I)]
    assert not leftovers, f"русские буквы в таджикской локали: {leftovers}"


# Всё, что человек вводит, сверяется с табелем и таблицами, а там латиница.
# Кнопки могут быть на любом языке, вводить — только по-английски.
ASKS_FOR_INPUT = (
    "driver.enter_name", "driver.enter_car", "driver.enter_plates",
    "driver.ask_city", "passengers.enter", "leave.ask_name",
    "search.ask_name", "search.ask_city",
)
NOT_FOUND_ERRORS = (
    "driver.name_not_in_employees", "driver.name_suggestions",
    "leave.not_employee", "search.not_employee",
    "driver.city_not_found", "search.city_not_found",
)


def test_cleared_list_message_explains_how_to_get_the_menu_back():
    """Сообщение уходит без клавиатуры, на экране остаются старые «Да/Нет»."""
    for name, loc in ALL_LOCALES:
        assert "/start" in loc.STRINGS["weekly.expired_cleared"], name


def test_input_prompts_say_to_type_in_english():
    for name, loc in ALL_LOCALES:
        hint = loc.STRINGS["hint.english"]
        for key in ASKS_FOR_INPUT + NOT_FOUND_ERRORS + ("lang.switched", "help.footer"):
            assert loc.STRINGS[key].count(hint) == 1, f"{name}: {key}"


def test_passenger_not_found_mentions_english():
    """Предупреждений бывает до четырёх в одном сообщении, поэтому целая
    подсказка тут лишняя: достаточно слова внутри шага."""
    word = {"ru": "на английском", "en": "in English", "es": "en inglés",
            "tg": "англисӣ", "uz": "ingliz tilida"}
    for name, loc in ALL_LOCALES:
        assert word[name] in loc.STRINGS["passenger_warning.not_found"], name


def test_placeholders_match_between_locales():
    """{driver} в одной локали и {name} в другой — это KeyError в проде."""
    ph = lambda s: set(re.findall(r"\{(\w+)\}", s))
    for name, loc in TRANSLATIONS:
        bad = [k for k in ru.STRINGS if ph(ru.STRINGS[k]) != ph(loc.STRINGS[k])]
        assert not bad, f"разные подстановки в ru/{name}: {bad}"


def test_instructions_name_buttons_that_exist():
    """«Нажми X» в тексте обязано совпадать с подписью кнопки X в этом языке."""
    expected = {
        # На проверке на экране только «Да/Нет»: меню открывается через «Нет»,
        # без этой кнопки в тексте совет нажать «Удалить» никуда не ведёт.
        "weekly.greeting": ("btn.yes", "btn.no", "btn.remove_passenger", "btn.add_passengers"),
        "weekly.reminder": ("btn.yes", "btn.no", "btn.remove_passenger", "btn.add_passengers"),
        "weekly.unclear": ("btn.yes", "btn.no"),
        "weekly.expired_cleared": ("btn.add_passengers",),
        "driver.ask_phone": ("btn.skip",),
        "driver.already_registered": ("btn.stop_being_driver",),
        "start.greeting": ("btn.help",),
        "start.role_passenger": ("btn.become_driver",),
        "help.footer": ("btn.language",),
    }
    for name, loc in ALL_LOCALES:
        for key, buttons in expected.items():
            for b in buttons:
                assert loc.STRINGS[b] in loc.STRINGS[key], (
                    f"{name}: в {key} нет подписи кнопки {b}"
                )


# ─────────────────────────── меню по ролям ───────────────────────────

class _Cfg:
    ADMIN_USER_IDS: list[int] = []


def _menu(is_driver):
    from handlers import BotHandlers

    h = BotHandlers.__new__(BotHandlers)
    h.config = _Cfg()
    h._role_cache = {}
    kb = h.kb_main(user_id=1, is_driver=is_driver)
    return [b.text for row in kb.keyboard for b in row]


def test_language_button_is_in_every_menu():
    for role in (True, False, None):
        assert ru.STRINGS["btn.language"] in _menu(role)


def test_language_is_no_longer_a_slash_command():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert 'CommandHandler("english"' not in src
    assert 'CommandHandler("russian"' not in src
    for _, loc in ALL_LOCALES:
        assert "/english" not in loc.STRINGS["help.footer"]
        assert "/russian" not in loc.STRINGS["help.footer"]


def test_driver_menu_hides_irrelevant_actions():
    texts = _menu(True)
    assert ru.STRINGS["btn.add_passengers"] in texts
    assert ru.STRINGS["btn.stop_being_driver"] in texts
    # Водителю незачем «Стать водителем» и «Я больше не еду с водителем»
    assert ru.STRINGS["btn.become_driver"] not in texts
    assert ru.STRINGS["btn.leave_carpool"] not in texts


def test_passenger_menu_hides_driver_actions():
    texts = _menu(False)
    assert ru.STRINGS["btn.become_driver"] in texts
    assert ru.STRINGS["btn.leave_carpool"] in texts
    # Это и был источник тупика «Ты не зарегистрирован как водитель»
    assert ru.STRINGS["btn.add_passengers"] not in texts
    assert ru.STRINGS["btn.remove_passenger"] not in texts
    assert ru.STRINGS["btn.stop_being_driver"] not in texts


def test_unknown_role_shows_everything():
    """Роль неизвестна — ничего не прячем, чтобы人 не потерял нужную кнопку."""
    texts = _menu(None)
    for key in ("btn.become_driver", "btn.add_passengers", "btn.my_record",
                "btn.remove_passenger", "btn.find_driver", "btn.leave_carpool",
                "btn.stop_being_driver"):
        assert ru.STRINGS[key] in texts, key


def test_help_button_always_present():
    for role in (True, False, None):
        assert ru.STRINGS["btn.help"] in _menu(role)


def test_role_cache_expires():
    import time as _time

    from handlers import BotHandlers

    h = BotHandlers.__new__(BotHandlers)
    h._role_cache = {}
    h.remember_role(7, True)
    assert h.known_role(7) is True
    h._role_cache[7] = (_time.time() - BotHandlers._ROLE_TTL - 1, True)
    assert h.known_role(7) is None, "протухшая роль должна забываться"


def test_role_cache_unknown_user():
    from handlers import BotHandlers

    h = BotHandlers.__new__(BotHandlers)
    h._role_cache = {}
    assert h.known_role(123) is None
    assert h.known_role(None) is None


# ─────────────────────── карточка водителя в поиске ───────────────────────

def _card(**kw):
    from handlers import BotHandlers
    from models import Driver

    defaults = dict(name="Aldar Rakshaev", tg_id=1, username="aldar",
                    phone="", car="Toyota RAV4", plates="ABC123",
                    shift="Day", city="San Jose", state="CA")
    defaults.update(kw)
    taken = defaults.pop("taken", None)
    h = BotHandlers.__new__(BotHandlers)
    return h._driver_card(Driver(**defaults), viewer_id=None, taken=taken)


def test_card_shows_shift_and_free_seats():
    card = _card(taken=2)
    assert "Aldar Rakshaev" in card
    assert "☀️ день" in card, "смена должна быть видна"
    assert "свободно 2 из 4" in card
    assert "t.me/aldar" in card


def test_card_marks_full_car():
    """Пассажир не должен писать тому, у кого мест нет."""
    assert "мест нет" in _card(taken=4)
    assert "свободно" not in _card(taken=4)


def test_card_night_shift():
    assert "🌙 ночь" in _card(shift="Night", taken=0)


def test_card_without_contact_says_so():
    card = _card(username="", phone="", taken=1)
    assert "контакта нет" in card
    assert "t.me/" not in card


def test_card_phone_shown_when_given():
    """Номер приводится к единому виду, как бы его ни записали в таблице."""
    card = _card(username="", phone="+1 408 555 0101", taken=1)
    assert "📞 (408) 555-0101" in card
    assert "контакта нет" not in card


def test_card_hides_garbage_in_the_phone_column():
    """Реальный случай: в поле телефона оказался список пассажиров.

    Бот показывал его в карточке после значка телефона. Теперь такое
    поле считается пустым — лучше «контакта нет», чем чужие фамилии
    под видом номера.
    """
    card = _card(username="", phone="ПАССАЖИРЫ. SHARAFJON YULDOSHEV ISLAM", taken=1)
    assert "📞" not in card
    assert "контакта нет" in card


def test_card_without_seat_data_omits_seats():
    """Если число пассажиров не удалось получить — просто не пишем про места."""
    card = _card(taken=None)
    assert "свободно" not in card and "мест нет" not in card
    assert "Toyota RAV4" in card


def test_no_duplicate_keys_in_locales():
    """Дубль ключа молча отбрасывает одну из записей.

    Реальный случай: 'start.greeting' был объявлен дважды, побеждала вторая
    короткая запись — длинное приветствие с подсказкой по роли не доходило
    до пользователя вообще, а переданный role_hint тихо игнорировался.
    Словарь Python такое не сигнализирует, поэтому проверяем исходник.
    """
    import ast
    import collections

    for name in LOCALE_FILES:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            dupes = [k for k, c in collections.Counter(keys).items() if c > 1]
            assert not dupes, f"{name}: ключ объявлен дважды: {dupes}"
            break


# ───────────────────────── контакт администратора ─────────────────────────

# Говорит об администраторах как о группе («команда только для админов»),
# а не отправляет пользователя к конкретному человеку.
ADMIN_GROUP_KEYS = {"admin.not_authorized"}


def test_admin_handle_lives_in_one_place():
    """Хэндл не должен быть вписан литералом в локаль.

    Он встречается в десяти репликах каждого локаля. Литералом это двадцать
    правок при смене администратора — и одна забытая, которая будет годами
    отправлять людей не туда. Единственное место — locales/contacts.py.
    """
    from locales.contacts import ADMIN_CONTACT

    for name in LOCALE_FILES:
        src = (ROOT / name).read_text(encoding="utf-8")
        assert ADMIN_CONTACT not in src, (
            f"{name}: хэндл вписан литералом — подставляй {{_ADMIN}}"
        )


def test_no_faceless_admin_references():
    """«Обратись к администратору» без имени — тупик для пользователя.

    Человек получает ошибку и не знает, кому писать. Если реплика отправляет
    к администратору, в ней должен стоять его хэндл.
    """
    import re as _re

    pattern = _re.compile(
        r"администратор|маъмур|administrator|administrador|the admin\b", _re.I)
    for loc_name, loc in ALL_LOCALES:
        bad = [k for k, v in loc.STRINGS.items()
               if k not in ADMIN_GROUP_KEYS and pattern.search(v)]
        assert not bad, f"{loc_name}: отправляют к администратору без хэндла: {bad}"


def test_admin_contact_is_substituted_not_left_as_placeholder():
    """Ловит f-строку, которую забыли пометить префиксом f.

    Без префикса в чат уедет буквальное '{_ADMIN}'. Для пользователя это
    выглядит как сломанный бот, а тест симметрии локалей такое пропускает:
    скобки-то есть в обеих локалях одинаково.
    """
    from locales.contacts import ADMIN_CONTACT

    for loc_name, loc in ALL_LOCALES:
        leaked = [k for k, v in loc.STRINGS.items() if "_ADMIN" in v]
        assert not leaked, f"{loc_name}: подстановка не выполнилась: {leaked}"
        assert any(ADMIN_CONTACT in v for v in loc.STRINGS.values()), (
            f"{loc_name}: хэндл не попал ни в одну реплику"
        )


# ───────────────────────── сборка текста из причин ─────────────────────────

def _render(reasons, lang):
    """Рендер причин глазами пользователя с заданным языком."""
    import i18n
    from handlers import BotHandlers

    original = i18n.get_user_lang
    i18n.get_user_lang = lambda tg_id, state_file="bot_state.json": lang
    try:
        return BotHandlers.__new__(BotHandlers)._render_reasons(reasons, 1)
    finally:
        i18n.get_user_lang = original


def test_reason_renders_in_the_readers_language():
    """Ровно то, что чинили: ответ не должен быть на двух языках сразу."""
    from models import Reason

    line, = _render([Reason("passenger_warning.wrong_shift",
                            {"name": "Ivan", "driver_shift": "day",
                             "passenger_shift": "night"})], "en")
    assert "Ivan" in line
    assert not re.search(r"[а-яё]", line, re.I), f"русский в английском ответе: {line}"


def test_same_reason_in_russian():
    from models import Reason

    line, = _render([Reason("passenger_warning.wrong_shift",
                            {"name": "Иван", "driver_shift": "day",
                             "passenger_shift": "night"})], "ru")
    assert "другой смене" in line


def test_wrong_shift_names_shifts_in_the_readers_language():
    """Причина несёт код смены, а показывать надо название, а не meltech_day."""
    from models import Reason

    r = Reason("passenger_warning.wrong_shift", {
        "name": "Ivan", "driver_shift": "day", "passenger_shift": "meltech_night",
    })
    for code, loc in ALL_LOCALES:
        line, = _render([r], code)
        assert loc.STRINGS["shift.day"] in line, code
        assert loc.STRINGS["shift.meltech_night"] in line, code
        assert "meltech_night" not in line, code


def test_detach_hint_uses_the_button_name_of_that_language():
    """Подсказка называет кнопку — значит её нельзя зашить в локаль отдельно.

    Водитель-англичанин должен прочитать английское название кнопки,
    иначе инструкция «нажми X» указывает на несуществующий пункт меню.
    """
    from models import Reason

    r = Reason("passenger_warning.already_with_driver",
               {"name": "Ivan", "driver": "Petr"})
    en_line, = _render([r], "en")
    ru_line, = _render([r], "ru")

    assert en.STRINGS["btn.leave_carpool"] in en_line
    assert ru.STRINGS["btn.leave_carpool"] in ru_line
    assert "Petr" in en_line, "имя чужого водителя должно остаться"


def test_reason_without_params_renders():
    from models import Reason

    line, = _render([Reason("passenger_warning.too_many")], "en")
    assert line == en.STRINGS["passenger_warning.too_many"]


# ─────────────── смена роли должна сразу менять меню ───────────────

def test_menu_switches_right_after_quitting_as_driver():
    """Кэш роли живёт 15 минут — и это ловушка при смене роли.

    Реальный случай: человек нажал «Перестать быть водителем», получил
    «Готово, ты больше не водитель» — и клавиатуру с кнопками «Добавить
    пассажиров» и «Удалить пассажира». Кэш всё ещё отвечал «водитель»,
    потому что обработчик удалял запись, но кэш не трогал.
    """
    import asyncio
    from types import SimpleNamespace

    from handlers import BotHandlers

    class _Sheets:
        def get_driver_passengers(self, tg_id): return None
        def get_driver(self, tg_id): return None
        def delete_driver_passengers(self, tg_id): return True
        def delete_driver(self, tg_id): return None
        def clear_rides_with(self, names): return 0

    h = BotHandlers.__new__(BotHandlers)
    h.config = _Cfg()
    h._role_cache = {}
    h.sheets = _Sheets()
    h.remember_role(7, True)

    seen = {}

    async def _reply(update, text, **kwargs):
        seen["markup"] = kwargs.get("reply_markup")

    async def _log_admin(*args, **kwargs):
        return None

    h._reply = _reply
    h.log_admin = _log_admin

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        effective_message=SimpleNamespace(text=ru.STRINGS["btn.yes"]),
    )
    asyncio.run(h.stop_being_driver_confirm(update, None))

    assert h.known_role(7) is False, "кэш не узнал о смене роли"
    texts = [b.text for row in seen["markup"].keyboard for b in row]
    assert ru.STRINGS["btn.add_passengers"] not in texts, "меню осталось водительским"
    assert ru.STRINGS["btn.stop_being_driver"] not in texts
    assert ru.STRINGS["btn.become_driver"] in texts


# ──────────────── «Пропустить» мимо кнопки ────────────────

def test_skip_recognised_when_typed_or_emoji_differs():
    """Двое реально записались телефоном, промахнувшись мимо кнопки.

    Один написал «Пропустить» словом, вторая отправила «⏭️» — с
    селектором варианта, тогда как в кнопке «⏭» без него.
    """
    from handlers import _means_skip

    assert _means_skip(ru.STRINGS["btn.skip"])
    assert _means_skip("Пропустить")
    assert _means_skip("пропустить")
    assert _means_skip("skip")
    assert _means_skip("⏭️")
    assert _means_skip("⏭")


def test_real_input_is_not_mistaken_for_skip():
    from handlers import _means_skip

    assert not _means_skip("7187152475")
    assert not _means_skip("+1 718 715 2475")
    assert not _means_skip("Ivan Ivanov")
    assert not _means_skip("")


def test_delta_sign_is_readable():
    """«+-4» выглядит как опечатка, а не как удаление четырёх строк."""
    from handlers import _delta

    assert _delta(21) == "+21"
    assert _delta(-4) == "−4"
    assert _delta(0) == "без изменений"
