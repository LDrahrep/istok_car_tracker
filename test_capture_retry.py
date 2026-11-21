"""Вечерний снимок переживает обрыв связи с Google.

17.11.2026 в 21:05 чтение листа упало с RemoteDisconnected: повтор ловил только
ответы Google с кодом ошибки (APIError), а оборванное соединение — нет, и до
перезапуска бота снимок за день так и не появился.
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests

import handlers as H
import sheets as S


def _manager():
    sm = S.SheetManager.__new__(S.SheetManager)
    sm._spreadsheet = object()
    sm._ws_cache = {}
    sm._op_cache = {}
    return sm


def _dropped():
    return requests.exceptions.ConnectionError(
        ("Connection aborted.", ConnectionResetError("Remote end closed connection without response")))


def test_retry_repeats_a_dropped_connection(monkeypatch):
    sleeps = []
    monkeypatch.setattr(S.time, "sleep", sleeps.append)
    calls = []

    def read():
        calls.append(1)
        if len(calls) < 3:
            raise _dropped()
        return "values"

    assert _manager()._retry(read) == "values"
    assert len(calls) == 3
    assert len(sleeps) == 2


def test_retry_gives_up_after_the_limit(monkeypatch):
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    calls = []

    def read():
        calls.append(1)
        raise _dropped()

    with pytest.raises(requests.exceptions.ConnectionError):
        _manager()._retry(read)
    assert len(calls) == S._RETRY_MAX + 1


def test_retry_does_not_repeat_a_programming_error(monkeypatch):
    monkeypatch.setattr(S.time, "sleep", lambda s: pytest.fail("не должен ждать"))
    calls = []

    def read():
        calls.append(1)
        raise ValueError("bad range")

    with pytest.raises(ValueError):
        _manager()._retry(read)
    assert len(calls) == 1


def _handlers():
    h = H.BotHandlers.__new__(H.BotHandlers)
    h.config = SimpleNamespace(ADMIN_CHAT_ID=42, DRIVERS_PASSENGERS_SHEET="drivers_passengers")
    h.sheets = object()
    return h


def _context(job_data=None):
    sent, scheduled = [], []

    async def send_message(chat_id, text, **kw):
        sent.append(text)

    def run_once(callback, when, data=None, name=None):
        scheduled.append({"callback": callback, "when": when, "data": data, "name": name})

    return SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        job_queue=SimpleNamespace(run_once=run_once),
        job=SimpleNamespace(data=job_data),
        sent=sent, scheduled=scheduled,
    )


@pytest.fixture
def failing_capture(monkeypatch):
    from db import store

    monkeypatch.setattr(store, "enabled", lambda: True)

    def capture_live(sheets, sheet_name):
        raise _dropped()

    monkeypatch.setattr(store, "capture_live", capture_live)


def test_a_failed_evening_capture_is_tried_again_in_ten_minutes(failing_capture):
    h, ctx = _handlers(), _context()
    asyncio.run(h.db_capture_job(ctx))
    assert len(ctx.scheduled) == 1
    retry = ctx.scheduled[0]
    assert retry["callback"] == h.db_capture_catchup
    assert retry["when"] == timedelta(minutes=10)
    assert retry["data"] == {"attempt": 1}
    assert "НЕ записан" in ctx.sent[0] and "10 мин" in ctx.sent[0]


def test_the_retry_counts_its_attempts(failing_capture):
    h, ctx = _handlers(), _context(job_data={"attempt": 2})
    asyncio.run(h.db_capture_job(ctx))
    assert [r["data"] for r in ctx.scheduled] == [{"attempt": 3}]


def test_retries_stop_after_three(failing_capture):
    h, ctx = _handlers(), _context(job_data={"attempt": 3})
    asyncio.run(h.db_capture_job(ctx))
    assert ctx.scheduled == []
    assert "НЕ записан" in ctx.sent[0]
