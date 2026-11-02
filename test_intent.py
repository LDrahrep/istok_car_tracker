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

SPANISH_YES = [
    "si", "sí", "Sí", "SI", "sí!", "¡sí!", "sii", "claro", "vale", "correcto",
    "está bien", "esta bien", "todo bien", "todo correcto", "todo igual",
    "sigue igual", "sin cambios", "de acuerdo", "confirmo", "exacto",
    "igual que antes",
]

SPANISH_NO = [
    "no", "No", "nop", "incorrecto", "no es correcto", "no está bien",
    "ya no", "ya no conduzco", "ya no los llevo", "cambió", "cambiaron",
    "han cambiado", "hay cambios", "borrar", "quitar a uno", "eliminar",
    "lista nueva", "pasajeros nuevos",
]

# Слово «no» здесь означает согласие: ничего не менялось.
SPANISH_NOTHING_CHANGED = [
    "no ha cambiado", "no han cambiado", "no ha cambiado nada",
    "no hay cambios", "nada ha cambiado", "nada cambió", "no cambió nada",
]

# На телефоне таджикские буквы часто заменяют русскими: «ха» вместо «ҳа».
TAJIK_YES = [
    "ҳа", "Ҳа", "ха", "бале", "оре", "дуруст", "хуб", "ҳама дуруст",
    "ҳамааш дуруст", "дуруст аст", "бе тағйир", "бетағйир", "тағйир наёфт",
    "тагйир наёфт", "иваз нашуд", "тағйирот нест",
]

TAJIK_NO = [
    "не", "нест", "нодуруст", "дуруст нест", "тағйир ёфт", "иваз шуд",
    "дигар намебарам", "дигар намеронам", "хато",
]

# Узбекский: апостроф пишут как угодно, а кириллицей пишут старшие.
UZBEK_YES = [
    "ha", "Ha", "xa", "xo'p", "xoʻp", "mayli", "to'g'ri", "toʻgʻri", "togri",
    "hammasi to'g'ri", "hammasi yaxshi", "hammasi joyida", "o'zgarmadi",
    "oʻzgarmadi", "o'zgarish yo'q", "oʻzgarishsiz",
    "ҳа", "хўп", "майли", "тўғри", "ҳаммаси тўғри", "ўзгармади",
]

UZBEK_NO = [
    "yo'q", "yoʻq", "yo’q", "yoq", "Yo'q", "YO'Q", "yuq", "noto'g'ri",
    "to'g'ri emas", "o'zgardi", "endi bormaydi", "boshqa",
    "йўқ", "йук", "нотўғри", "тўғри эмас", "ўзгарди",
]

# Должны остаться непонятыми: имена пассажиров, жалобы, посторонние вопросы.
# «Нету кнопок ниже» начинается с «нету» — без ограничения на длину ответа
# это классифицировалось бы как 'no' и стирало список.
MUST_STAY_UNCLEAR = [
    "Ivan Ivanov", "Sandzhi Erentsenov", "Baatr Nimgirov",
    "Нету кнопок ниже", "Lexus CT200H", "705MSU",
    "а когда зарплата", "when is payday", "hello", "",
    "Gilberto Cuevas", "Maria Lamas", "¿cuándo es el pago?", "hola",
    "no tengo los botones aquí",
    "Tohirjon Mirzoev", "Yo'ldosh Karimov", "Шумо кистед", "pul qachon beriladi",
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


def test_spanish_yes():
    for text in SPANISH_YES:
        assert parse(text) == "yes", f"{text!r} должно быть yes"


def test_spanish_no():
    for text in SPANISH_NO:
        assert parse(text) == "no", f"{text!r} должно быть no"


def test_spanish_nothing_changed_is_yes():
    for text in SPANISH_NOTHING_CHANGED:
        assert parse(text) == "yes", f"{text!r} должно быть yes"


def test_tajik_yes():
    for text in TAJIK_YES:
        assert parse(text) == "yes", f"{text!r} должно быть yes"


def test_tajik_no():
    for text in TAJIK_NO:
        assert parse(text) == "no", f"{text!r} должно быть no"


def test_uzbek_yes():
    for text in UZBEK_YES:
        assert parse(text) == "yes", f"{text!r} должно быть yes"


def test_uzbek_no():
    for text in UZBEK_NO:
        assert parse(text) == "no", f"{text!r} должно быть no"


def test_yes_no_buttons_of_every_language_are_understood():
    """Кнопка приходит как обычный текст: её подпись обязана разбираться."""
    from locales import LOCALES

    for code, strings in LOCALES.items():
        assert parse(strings["btn.yes"]) == "yes", code
        assert parse(strings["btn.no"]) == "no", code


def test_ambiguous_stays_unclear():
    for text in MUST_STAY_UNCLEAR:
        assert parse(text) == "unclear", f"{text!r} должно остаться unclear"


def test_button_labels_with_emoji():
    """Кнопки приходят вместе с эмодзи — это основной путь ответа."""
    assert parse("✅ Да") == "yes"
    assert parse("❌ Нет") == "no"
    assert parse("✅ Yes") == "yes"
    assert parse("❌ No") == "no"
    assert parse("✅ Sí") == "yes"


def test_negation_beats_positive_substring():
    """«не актуально» содержит «актуально», «not correct» содержит «correct»."""
    assert parse("не актуально") == "no"
    assert parse("неактуально") == "no"
    assert parse("not correct") == "no"
    assert parse("not good") == "no"
