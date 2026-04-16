"""Per-user i18n (Russian + English).

Usage:
    from i18n import t, button, get_user_lang, set_user_lang

    # Get localized string (auto-detects user's lang)
    await update.message.reply_text(t("driver.enter_name", tg_id=update.effective_user.id))

    # Get localized button text
    button("btn.become_driver", tg_id)

    # Switch user's language
    set_user_lang(tg_id, "en")
"""
from __future__ import annotations

from typing import Optional

from locales import LOCALES, DEFAULT_LANG


def get_user_lang(tg_id: Optional[int], state_file: str = "bot_state.json") -> str:
    """Return user's language code, falling back to default."""
    if tg_id is None:
        return DEFAULT_LANG
    from persistence import get_state_manager
    state = get_state_manager(state_file)
    lang = state.get_language(tg_id)
    return lang if lang in LOCALES else DEFAULT_LANG


def set_user_lang(tg_id: int, lang: str, state_file: str = "bot_state.json") -> None:
    """Persist user's language preference."""
    from persistence import get_state_manager
    state = get_state_manager(state_file)
    state.set_language(tg_id, lang)


def t(key: str, tg_id: Optional[int] = None, lang: Optional[str] = None, **kwargs) -> str:
    """Translate a string key to user's language.

    If `lang` is provided, uses it directly. Otherwise resolves from `tg_id`.
    Falls back to Russian if key is missing in the target locale.
    """
    if lang is None:
        lang = get_user_lang(tg_id)

    locale = LOCALES.get(lang, LOCALES[DEFAULT_LANG])
    template = locale.get(key)
    if template is None:
        template = LOCALES[DEFAULT_LANG].get(key, f"[{key}]")

    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


def button(key: str, tg_id: Optional[int] = None, lang: Optional[str] = None) -> str:
    """Get button text for user's language. Alias for t() for clarity."""
    return t(key, tg_id=tg_id, lang=lang)


def all_translations(key: str) -> list[str]:
    """Return all translations of a key across all locales.

    Useful for regex matchers in bot.py that need to accept any language.
    """
    result = []
    seen = set()
    for loc in LOCALES.values():
        val = loc.get(key)
        if val and val not in seen:
            result.append(val)
            seen.add(val)
    return result


def is_button(text: str, key: str) -> bool:
    """Check if `text` matches the button for `key` in any supported locale."""
    if text is None:
        return False
    return text in all_translations(key)


def button_regex(*keys: str) -> str:
    """Build regex alternation pattern matching buttons in any locale.

    Example:
        button_regex("btn.yes", "btn.no")
        -> "(\\✅\\ Да|\\✅\\ Yes|\\❌\\ Нет|\\❌\\ No)"

    Handles multiple keys for keyboards with multiple options.
    """
    import re as _re
    variants = []
    seen = set()
    for key in keys:
        for v in all_translations(key):
            if v not in seen:
                variants.append(_re.escape(v))
                seen.add(v)
    return "|".join(variants)
