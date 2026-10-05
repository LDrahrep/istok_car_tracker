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
