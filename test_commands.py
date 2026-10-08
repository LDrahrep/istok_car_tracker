"""Дымовые тесты админских команд.

Повод: /db_report падал с «name 'strict' is not defined» — осталась ссылка
на переменную, переименованную при смене правила. test_attrs такое не
ловит: он сверяет обращения к self.X, а это обычная локальная переменная,
и ошибка возникает только при вызове.

Поэтому здесь команды действительно вызываются. Внешние зависимости
подменяются, проверяется не результат, а то, что обработчик доходит
до конца и ничего не роняет.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import handlers as H

ROOT = Path(__file__).parent
ADMIN_ID = 7


class _Cfg:
    ADMIN_USER_IDS = [ADMIN_ID]
    ADMIN_CHAT_ID = 0
    DRIVERS_SHEET = "drivers"
    EMPLOYEES_SHEET = "employees"
    DRIVERS_PASSENGERS_SHEET = "drivers_passengers"


def _bot(sheets=None):
    h = H.BotHandlers.__new__(H.BotHandlers)
    h.config = _Cfg()
    h._role_cache = {}
    h.sheets = sheets or SimpleNamespace()
    sent: list[str] = []

    async def _reply(update, text, **kw):
        sent.append(text)

    async def _log(*a, **k):
        return None

    h._reply = _reply
    h.log_admin = _log
    return h, sent


def _update():
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        effective_message=SimpleNamespace(text=""),
    )


def _ctx(*args):
    return SimpleNamespace(args=list(args), bot=SimpleNamespace())


# Команды, которые без аргументов обязаны показать подсказку и выйти,
# не трогая ни Google, ни базу.
NO_ARG_USAGE = ["db_report", "db_merge", "db_site_rename", "whois", "unlink"]


@pytest.mark.parametrize("name", NO_ARG_USAGE)
def test_usage_branch_does_not_crash(name):
    h, sent = _bot()
    asyncio.run(getattr(h, f"{name}_command")(_update(), _ctx()))
    assert sent, f"/{name} без аргументов ничего не ответил"


def test_admin_index_renders():
    h, sent = _bot()
    asyncio.run(h.admin_command(_update(), _ctx()))
    assert sent and "/whois" in sent[0]


def test_non_admin_gets_no_answer():
    """Админская команда для чужого — молчание, а не отказ."""
    h, sent = _bot()
    upd = _update()
    upd.effective_user.id = 999999
    asyncio.run(h.admin_command(upd, _ctx()))
    assert not sent


@pytest.mark.parametrize("хвост,ожидание", [
    ([], "fair"),
    (["строго"], "strict"),
    (["табель"], "timesheet"),
])
def test_report_picks_the_rule_and_runs(monkeypatch, хвост, ожидание):
    """Ровно тот путь, на котором падало: разбор слова и вызов расчёта."""
    from db import svodka

    увидено = {}

    def fake_export(sheets, start, end, *, mode="fair"):
        увидено["mode"] = mode
        return {"enabled": True, "weeks": ["09/28 - 10/04"], "written": {},
                "unmarked": [], "mode": mode}

    monkeypatch.setattr(svodka, "export", fake_export)
    h, sent = _bot()
    asyncio.run(h.db_report_command(
        _update(), _ctx("2026-09-28", "2026-10-04", *хвост)))

    assert увидено.get("mode") == ожидание
    assert any("Сводка" in t for t in sent)


def test_report_rejects_bad_dates():
    h, sent = _bot()
    asyncio.run(h.db_report_command(_update(), _ctx("вчера", "сегодня")))
    assert any("ГГГГ-ММ-ДД" in t for t in sent)


def test_every_command_handler_exists():
    """Регистрация ссылается на существующий метод."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    for name in re.findall(r"handlers\.([a-z_]+_command)", src):
        assert hasattr(H.BotHandlers, name), f"bot.py зовёт несуществующий {name}"
