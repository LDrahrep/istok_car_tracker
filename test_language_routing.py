"""Маршрутизация выбора языка на настоящем приложении.

Юнит-тесты вызывают обработчик напрямую и не видят главного: дойдёт ли до
него нажатие. Здесь проверяется порядок обработчиков в build_app — кнопка
посреди диалога не должна уйти в диалог как введённое имя, а ответ на
инлайн-кнопку не должен потеряться.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from telegram import CallbackQuery, Chat, Message, MessageEntity, Update, User
from telegram.ext import ConversationHandler

import bot as bot_module
import persistence
from config import Config
from handlers import ST_ADD_PASSENGERS

USER_ID = 111


@pytest.fixture
def app(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    monkeypatch.setattr(persistence, "_state_mgr", None)
    monkeypatch.setattr(bot_module, "Config", lambda: Config(
        TELEGRAM_BOT_TOKEN="123:abc", STATE_FILE=state_file))
    monkeypatch.setattr(bot_module, "SheetManager", lambda config: SimpleNamespace())
    # Опрос Telegram и расписание здесь не нужны, а Updater и JobQueue из
    # ptb 20.7 не собираются под локальным Python 3.14 (в проде 3.11).
    monkeypatch.setattr("telegram.ext._applicationbuilder.Updater", lambda **kw: None)
    monkeypatch.setattr("telegram.ext._applicationbuilder.JobQueue", MagicMock)
    application = bot_module.build_app()
    # Разбору /команд нужен username бота: без initialize() его берём сами.
    application.bot._bot_user = User(id=1, first_name="B", is_bot=True, username="b_bot")
    yield application
    monkeypatch.setattr(persistence, "_state_mgr", None)


def _user():
    return User(id=USER_ID, first_name="T", is_bot=False)


def _chat():
    return Chat(id=USER_ID, type="private")


def _text(app, text):
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=_chat(),
                  from_user=_user(), text=text)
    msg.set_bot(app.bot)
    return Update(update_id=1, message=msg)


def _tap(app, data):
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=_chat(),
                  from_user=_user(), text="?")
    query = CallbackQuery(id="1", from_user=_user(), chat_instance="1",
                            data=data, message=msg)
    query.set_bot(app.bot)
    return Update(update_id=2, callback_query=query)


def _route(app, update):
    """Имена обработчиков, сработавших бы по группам, как в process_update."""
    hit = []
    for group in sorted(app.handlers):
        for handler in app.handlers[group]:
            if handler.check_update(update) not in (None, False):
                name = type(handler).__name__ if isinstance(handler, ConversationHandler) \
                    else getattr(handler.callback, "__name__", "?")
                hit.append((group, name))
                break
    return hit


def _in_dialog(app, state):
    conv = next(h for h in app.handlers[0] if isinstance(h, ConversationHandler))
    conv._conversations[(USER_ID, USER_ID)] = state


def _group0(app, update):
    return [name for group, name in _route(app, update) if group == 0]


@pytest.mark.parametrize("label", ["🌐 Язык", "🌐 Language", "🌐 Idioma", "🌐 Забон", "🌐 Til"])
def test_language_button_opens_the_chooser(app, label):
    assert _group0(app, _text(app, label)) == ["language_menu"]


def test_language_button_mid_dialog_is_not_taken_for_a_name(app):
    _in_dialog(app, ST_ADD_PASSENGERS)
    assert _group0(app, _text(app, "🌐 Idioma")) == ["language_menu"]


def test_dialog_still_gets_plain_text(app):
    _in_dialog(app, ST_ADD_PASSENGERS)
    assert _group0(app, _text(app, "Juan Perez")) == ["ConversationHandler"]


@pytest.mark.parametrize("code", ["ru", "en", "es", "tg", "uz"])
def test_tap_on_a_language_is_routed(app, code):
    assert _group0(app, _tap(app, f"lang:{code}")) == ["language_chosen"]


def test_tap_works_mid_dialog(app):
    _in_dialog(app, ST_ADD_PASSENGERS)
    assert _group0(app, _tap(app, "lang:es")) == ["language_chosen"]


def test_unknown_language_is_ignored(app):
    assert _group0(app, _tap(app, "lang:xx")) == []


def test_language_button_is_not_a_weekly_answer(app):
    """Открытая проверка не должна принять кнопку за ответ на неё."""
    persistence.get_state_manager().is_pending = lambda tg_id: True
    assert _route(app, _text(app, "🌐 Idioma"))[1:] == [(0, "language_menu")]


@pytest.mark.parametrize("command", ["/english", "/russian"])
def test_old_slash_commands_are_gone(app, command):
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=_chat(),
                  from_user=_user(), text=command,
                  entities=(MessageEntity(MessageEntity.BOT_COMMAND, 0, len(command)),))
    msg.set_bot(app.bot)
    assert _group0(app, Update(update_id=3, message=msg)) == ["unknown"]
