"""Тесты разбора свободных ответов на еженедельную проверку.

Цена ошибки несимметрична: ложное «нет» СТИРАЕТ водителю список пассажиров,
а ложное «непонятно» всего лишь переспрашивает. Поэтому всё сомнительное
обязано оставаться 'unclear' — на это есть отдельный блок проверок.
"""
from __future__ import annotations

from intent import parse_yes_no_intent as parse

# Англоязычные водители массово получали «не понял» и переставали отвечать,
# после чего expire через 2 часа очищал им карпул. Эти формулировки — то,
# чем люди реально отвечают вместо нажатия кнопки.
ENGLISH_YES = [
    "yes", "yep", "yeah", "ok", "fine", "correct", "confirmed",
    "all good", "all right", "all set",
    "still good", "still the same", "same as before",
    "that's right", "thats right",
    "everything is fine", "keep it", "leave as is", "up to date",
    # Содержат отрицательные слова, но означают согласие — ловятся
    # приоритетным списком до проверки отрицаний.
    "nothing changed", "nothing has changed", "no changes",
]

ENGLISH_NO = [
    "no", "nope", "nah", "wrong", "outdated",
    "not correct", "not right", "not actual",
    "clear it", "delete all", "remove all", "remove them",
    "they changed", "it changed",
    "no longer driving", "i don't drive anymore", "i dont drive anymore",
    "need to update", "new list",
]

RUSSIAN_YES = [
    "да", "всё верно", "актуально", "пока да", "все ок",
    "ничего не менял", "без изменений", "так и оставить",
]

RUSSIAN_NO = [
    "нет", "не актуально", "неверно", "очистить",
    "больше не вожу", "поменялись",
]

# Должны остаться непонятыми: имена пассажиров, жалобы, посторонние вопросы.
# «Нету кнопок ниже» начинается с «нету» — без ограничения на длину ответа
# это классифицировалось бы как 'no' и стирало список.
MUST_STAY_UNCLEAR = [
    "Ivan Ivanov", "Sandzhi Erentsenov", "Baatr Nimgirov",
    "Нету кнопок ниже", "Lexus CT200H", "705MSU",
    "а когда зарплата", "when is payday", "hello", "",
]


def test_english_yes():
    for text in ENGLISH_YES:
        assert parse(text) == "yes", f"{text!r} должно быть yes"


def test_english_no():
    for text in ENGLISH_NO:
        assert parse(text) == "no", f"{text!r} должно быть no"


def test_russian_yes():
    for text in RUSSIAN_YES:
        assert parse(text) == "yes", f"{text!r} должно быть yes"


def test_russian_no():
    for text in RUSSIAN_NO:
        assert parse(text) == "no", f"{text!r} должно быть no"


def test_ambiguous_stays_unclear():
    for text in MUST_STAY_UNCLEAR:
        assert parse(text) == "unclear", f"{text!r} должно остаться unclear"


def test_button_labels_with_emoji():
    """Кнопки приходят вместе с эмодзи — это основной путь ответа."""
    assert parse("✅ Да") == "yes"
    assert parse("❌ Нет") == "no"
    assert parse("✅ Yes") == "yes"
    assert parse("❌ No") == "no"


def test_negation_beats_positive_substring():
    """«не актуально» содержит «актуально», «not correct» содержит «correct»."""
    assert parse("не актуально") == "no"
    assert parse("неактуально") == "no"
    assert parse("not correct") == "no"
    assert parse("not good") == "no"
