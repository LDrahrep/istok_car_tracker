"""Тесты пользовательского слоя: ключи локалей и меню по ролям."""
from __future__ import annotations

import re
from pathlib import Path

from locales import en, ru

ROOT = Path(__file__).parent
SOURCES = ["handlers.py", "bot.py", "weekly.py", "report.py", "i18n.py"]

# Ключи, собираемые динамически (t(key) с переменной) — проверить статикой
# нельзя, перечисляем явно.
DYNAMIC_OK = {"help.driver", "help.passenger",
              "start.role_driver", "start.role_passenger"}


def _used_keys() -> set[str]:
    """Все литеральные ключи из вызовов t(...) и button(...)."""
    pattern = re.compile(r'\b(?:t|button)\(\s*["\']([a-z_]+\.[a-z_0-9]+)["\']')
    keys: set[str] = set()
    for name in SOURCES:
        keys |= set(pattern.findall((ROOT / name).read_text(encoding="utf-8")))
    return keys


def test_locales_are_symmetric():
    """Расхождение ключей = англоязычный пользователь увидит русский текст."""
    assert set(ru.STRINGS) == set(en.STRINGS), (
        f"только в ru: {sorted(set(ru.STRINGS) - set(en.STRINGS))}, "
        f"только в en: {sorted(set(en.STRINGS) - set(ru.STRINGS))}"
    )


def test_every_used_key_exists():
    """Ловит опечатки и выдуманные имена ключей до того, как их увидит пользователь.

    Без этого t('err.generic') молча вернёт '[err.generic]' прямо в чат.
    """
    missing = sorted(k for k in _used_keys() if k not in ru.STRINGS)
    assert not missing, f"ключи используются, но их нет в локали: {missing}"


def test_english_locale_has_no_russian():
    """Единственное исключение — сообщение о переключении НА русский."""
    offenders = [
        k for k, v in en.STRINGS.items()
        if isinstance(v, str) and re.search(r"[а-яё]", v, re.I) and k != "lang.switched_ru"
    ]
    assert not offenders, f"русский текст в английской локали: {offenders}"


def test_placeholders_match_between_locales():
    """{driver} в одной локали и {name} в другой — это KeyError в проде."""
    ph = lambda s: set(re.findall(r"\{(\w+)\}", s))
    bad = [k for k in ru.STRINGS if ph(ru.STRINGS[k]) != ph(en.STRINGS[k])]
    assert not bad, f"разные подстановки в ru/en: {bad}"


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
    card = _card(username="", phone="+1 408 555 0101", taken=1)
    assert "📞 +1 408 555 0101" in card
    assert "контакта нет" not in card


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

    for name in ("locales/ru.py", "locales/en.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            dupes = [k for k, c in collections.Counter(keys).items() if c > 1]
            assert not dupes, f"{name}: ключ объявлен дважды: {dupes}"
            break
