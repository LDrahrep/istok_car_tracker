"""Сценарии водителя: еженедельная проверка, подсказки, регистрация.

Каждый тест — случай из поддержки 20–24.10:
• правка списка вместо «Да» → через 2 часа список стирался вместе с новыми
  людьми;
• «Нет» на проверке стирал весь список, хотя водитель имел в виду «поменялся
  один»;
• «Нет» в диалоге «Перестать быть водителем» перехватывался проверкой и тоже
  стирал список;
• ночную смену спрашивали днём — спящий водитель терял список через 2 часа;
• после подсказки диалог закрывался, и подсказанное имя уходило в «не понял»;
• водитель регистрировался под именем своего пассажира.

Google и Telegram подменяются: проверяется логика обработчиков.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import handlers as H
import persistence
import weekly
from i18n import t
from models import Driver, DriverPassengers, Employee, Reason

DRIVER_ID = 111


@pytest.fixture
def state(tmp_path, monkeypatch):
    path = str(tmp_path / "state.json")
    monkeypatch.setattr(persistence, "_state_mgr", None)
    mgr = persistence.get_state_manager(path)
    yield path, mgr
    monkeypatch.setattr(persistence, "_state_mgr", None)


class _Sheets(SimpleNamespace):
    """Заглушка таблиц: любая запись, которой тест не ждал, — ошибка."""

    def upsert_driver_passengers(self, dp):
        raise AssertionError(f"неожиданная запись карточки: {dp}")


def _bot(state_path, sheets):
    h = H.BotHandlers.__new__(H.BotHandlers)
    h.config = SimpleNamespace(ADMIN_USER_IDS=[], ADMIN_CHAT_ID=0,
                               STATE_FILE=state_path)
    h._role_cache = {}
    h.sheets = sheets
    replies: list[tuple[str, object]] = []
    admin: list[tuple[str, str]] = []

    async def _reply(update, text, **kw):
        replies.append((text, kw.get("reply_markup")))

    async def _log(context, title, text="", update=None):
        admin.append((title, text))

    h._reply = _reply
    h.log_admin = _log
    return h, replies, admin


def _update(text, uid=DRIVER_ID):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid, username="u"),
        effective_message=SimpleNamespace(text=text),
    )


def _ctx():
    sent = []

    async def send_message(chat_id, text, **kw):
        sent.append((chat_id, text))

    return SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=send_message),
                           application=SimpleNamespace(bot_data={}), sent=sent)


def _keyboard_texts(markup) -> list[str]:
    if markup is None:
        return []
    return [str(getattr(b, "text", b)) for row in markup.keyboard for b in row]


DRIVER = Driver(name="Aldar Rakshaev", tg_id=DRIVER_ID, shift="Day")


# ─────────────────────── подсказки ───────────────────────

def test_suggestion_keeps_the_dialog_open_with_buttons(state):
    path, _ = state
    suggest = Reason("passenger_warning.not_found_suggest", {
        "name": "Abdurahimov Dzhamshed", "suggestions": "Dzhamshed Abdurakhimov",
        "options": ["Dzhamshed Abdurakhimov"]})
    sheets = _Sheets(validate_passengers=lambda tg, names: (
        [], [Reason("validate.nobody_added")], [suggest]))
    h, replies, _ = _bot(path, sheets)

    nxt = asyncio.run(h.add_passengers_input(_update("Abdurahimov Dzhamshed"), _ctx()))

    assert nxt == H.ST_ADD_PASSENGERS, "диалог должен ждать выбора, а не закрываться"
    assert "Dzhamshed Abdurakhimov" in _keyboard_texts(replies[-1][1])


def test_refusals_reach_the_admin_chat(state):
    """О том, что водитель не может добавить человека, мы узнавали из жалоб."""
    path, _ = state
    sheets = _Sheets(validate_passengers=lambda tg, names: (
        [], [Reason("validate.nobody_added")],
        [Reason("passenger_warning.not_found", {"name": "Brenda Neary"})]))
    h, _, admin = _bot(path, sheets)

    asyncio.run(h.add_passengers_input(_update("Brenda Neary"), _ctx()))

    assert any("Brenda Neary" in text for _, text in admin)


# ───────────── правка списка засчитывается как ответ ─────────────

def _saving_sheets(writes):
    return _Sheets(
        validate_passengers=lambda tg, names: ([Employee(name="Badma Matsakov", shift="Day")], [], []),
        get_driver=lambda tg: DRIVER,
        get_driver_passengers=lambda tg: DriverPassengers("Aldar Rakshaev", DRIVER_ID, ["Old One"]),
        upsert_driver_passengers=lambda dp: writes.append(list(dp.passengers)),
        assign_passengers_to_driver=lambda **kw: None,
        unlink_passenger_everywhere=lambda name, except_tgid: None,
    )


def test_saving_passengers_answers_the_weekly_check(state):
    path, mgr = state
    mgr.add_pending(DRIVER_ID, "day")
    writes = []
    h, _, _ = _bot(path, _saving_sheets(writes))

    asyncio.run(h.add_passengers_input(_update("Badma Matsakov"), _ctx()))

    assert writes, "список должен сохраниться"
    assert not mgr.is_pending(DRIVER_ID), "правка списка = список актуален"


def test_removing_a_passenger_answers_the_weekly_check(state):
    path, mgr = state
    mgr.add_pending(DRIVER_ID, "day")
    writes = []
    sheets = _Sheets(
        get_driver_passengers=lambda tg: DriverPassengers("Aldar Rakshaev", DRIVER_ID,
                                                          ["Old One", "Badma Matsakov"]),
        upsert_driver_passengers=lambda dp: writes.append(list(dp.passengers)),
        clear_rides_with=lambda names: None,
    )
    h, _, _ = _bot(path, sheets)

    asyncio.run(h.remove_passenger_input(_update("Old One"), _ctx()))

    assert writes == [["Badma Matsakov"]]
    assert not mgr.is_pending(DRIVER_ID)


# ───────────────────── ответ на проверку ─────────────────────

def test_no_on_weekly_check_keeps_the_list(state):
    """«Нет» — «что-то поменялось», а не «сотри всех»."""
    path, mgr = state
    mgr.add_pending(DRIVER_ID, "day")
    sheets = _Sheets(get_driver_passengers=lambda tg: DriverPassengers(
        "Aldar Rakshaev", DRIVER_ID, ["A A", "B B"]))
    h, replies, _ = _bot(path, sheets)

    asyncio.run(h.weekly_answer(_update("Нет"), _ctx()))

    assert not mgr.is_pending(DRIVER_ID)
    assert replies[-1][0] == t("weekly.no_answer", tg_id=DRIVER_ID,
                               add_button=H.button("btn.add_passengers", DRIVER_ID),
                               remove_button=H.button("btn.remove_passenger", DRIVER_ID))


def test_weekly_check_does_not_hijack_the_stop_driving_question(state):
    """«Нет» на «Точно перестать быть водителем?» стирал список пассажиров."""
    path, mgr = state
    mgr.add_pending(DRIVER_ID, "day")
    sheets = _Sheets(get_driver=lambda tg: DRIVER)
    h, _, _ = _bot(path, sheets)
    ctx = _ctx()

    asyncio.run(h.stop_being_driver_start(_update("🛑"), ctx))
    taken = asyncio.run(h.weekly_answer_priority(_update("Нет"), ctx))

    assert taken is False, "ответ принадлежит диалогу, а не проверке"
    assert mgr.is_pending(DRIVER_ID)


def test_weekly_answer_is_taken_when_no_other_question_is_open(state):
    path, mgr = state
    mgr.add_pending(DRIVER_ID, "day")
    h, _, _ = _bot(path, _Sheets())

    taken = asyncio.run(h.weekly_answer_priority(_update("Да"), _ctx()))

    assert taken is True
    assert not mgr.is_pending(DRIVER_ID)


# ─────────────────── молчание: напомнить, потом очистить ───────────────────

class _ExpireSheets(SimpleNamespace):
    def __init__(self, passengers):
        super().__init__(writes=[], cleared=[])
        self._passengers = passengers

    def get_driver_passengers(self, tg):
        return DriverPassengers("Aldar Rakshaev", tg, list(self._passengers))

    def upsert_driver_passengers(self, dp):
        self.writes.append(list(dp.passengers))

    def clear_rides_with(self, names):
        self.cleared.append(set(names))


def _age(mgr, hours):
    entry = mgr.state.pending_confirmations[str(DRIVER_ID)]
    entry["sent_at"] = time.time() - hours * 3600


def test_two_hours_of_silence_brings_a_reminder_not_a_wipe(state):
    path, mgr = state
    mgr.add_pending(DRIVER_ID, "night")
    _age(mgr, 3)
    sheets = _ExpireSheets(["A A", "B B"])
    ctx = _ctx()
    cfg = SimpleNamespace(ADMIN_CHAT_ID=0)

    asyncio.run(weekly.expire_unanswered(ctx.bot, sheets, mgr, cfg))

    assert sheets.writes == [], "через 2 часа список не трогаем"
    assert mgr.is_pending(DRIVER_ID)
    assert [chat for chat, _ in ctx.sent] == [DRIVER_ID], "напоминание водителю"

    asyncio.run(weekly.expire_unanswered(ctx.bot, sheets, mgr, cfg))
    assert len(ctx.sent) == 1, "напоминание одно, а не каждые 15 минут"


def test_a_day_of_silence_clears_the_list(state):
    path, mgr = state
    mgr.add_pending(DRIVER_ID, "night")
    _age(mgr, 25)
    sheets = _ExpireSheets(["A A", "B B"])
    ctx = _ctx()

    asyncio.run(weekly.expire_unanswered(ctx.bot, sheets, mgr, SimpleNamespace(ADMIN_CHAT_ID=0)))

    assert sheets.writes == [[]]
    assert not mgr.is_pending(DRIVER_ID)


# ─────────────────────── регистрация водителя ───────────────────────

def test_registration_asks_to_confirm_the_name_and_warns_about_a_passenger(state):
    """Mustafo записался под именем своего пассажира Akbar — бот должен был
    переспросить и сказать, что Akbar сейчас чей-то пассажир."""
    path, _ = state
    sheets = _Sheets(
        get_employee_by_name=lambda n: Employee(name="Akbar Azizmamadov", shift="Night"),
        is_name_taken_by_other_driver=lambda name, tg: False,
        find_driver_for_passenger=lambda name: (999, "Mustafo Aidarmamadov"),
    )
    h, replies, _ = _bot(path, sheets)
    ctx = _ctx()

    nxt = asyncio.run(h.become_driver_name(_update("Akbar Azizmamadov"), ctx))

    assert nxt == H.ST_DRIVER_NAME_CONFIRM
    assert "Mustafo Aidarmamadov" in replies[-1][0]
    assert "driver_name" not in ctx.user_data, "имя не принято до подтверждения"


def test_registration_yes_moves_on_to_the_car(state):
    path, _ = state
    sheets = _Sheets(
        get_employee_by_name=lambda n: Employee(name="Mustafo Aidarmamadov", shift="Night"),
        is_name_taken_by_other_driver=lambda name, tg: False,
        find_driver_for_passenger=lambda name: None,
    )
    h, _, _ = _bot(path, sheets)
    ctx = _ctx()
    asyncio.run(h.become_driver_name(_update("Mustafo Aidarmamadov"), ctx))

    nxt = asyncio.run(h.become_driver_name_confirm(_update("Да"), ctx))

    assert nxt == H.ST_DRIVER_CAR
    assert ctx.user_data["driver_name"] == "Mustafo Aidarmamadov"


def test_registration_no_asks_for_the_name_again(state):
    path, _ = state
    sheets = _Sheets(
        get_employee_by_name=lambda n: Employee(name="Akbar Azizmamadov", shift="Night"),
        is_name_taken_by_other_driver=lambda name, tg: False,
        find_driver_for_passenger=lambda name: None,
    )
    h, _, _ = _bot(path, sheets)
    ctx = _ctx()
    asyncio.run(h.become_driver_name(_update("Akbar Azizmamadov"), ctx))

    nxt = asyncio.run(h.become_driver_name_confirm(_update("Нет"), ctx))

    assert nxt == H.ST_DRIVER_NAME
    assert "driver_name" not in ctx.user_data


def test_registration_suggestions_keep_the_dialog_open(state):
    path, _ = state
    sheets = _Sheets(
        get_employee_by_name=lambda n: None,
        get_all_employees=lambda: [Employee(name="Mustafo Aidarmamadov", shift="Night")],
    )
    h, replies, _ = _bot(path, sheets)

    nxt = asyncio.run(h.become_driver_name(_update("Mustafo Aidarmamadof"), _ctx()))

    assert nxt == H.ST_DRIVER_NAME
    assert "Mustafo Aidarmamadov" in _keyboard_texts(replies[-1][1])


def test_self_warning_does_not_push_towards_stop_being_a_driver():
    """«Перестать быть водителем» удаляет карточку — советовать её за то, что
    водитель вписал себя, опасно."""
    for lang in ("ru", "en"):
        text = t("passenger_warning.self", lang=lang)
        assert "🛑" not in text


def test_registration_unknown_name_waits_for_another_try(state):
    """Текст говорит «напиши так же» — значит, бот должен ждать новый ввод,
    а не закрывать диалог."""
    path, _ = state
    sheets = _Sheets(get_employee_by_name=lambda n: None, get_all_employees=lambda: [])
    h, _, _ = _bot(path, sheets)

    nxt = asyncio.run(h.become_driver_name(_update("Совсем Неизвестный"), _ctx()))

    assert nxt == H.ST_DRIVER_NAME
