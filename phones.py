"""Разбор и приведение телефонов к читаемому виду.

Нужен потому, что колонку «Phone Number» заполняют руками, и туда попадает
что угодно. Живой пример: у одного водителя там оказался список пассажиров
(«ПАССАЖИРЫ. SHARAFJON YULDOSHEV …»), и бот честно показывал это в карточке
после значка телефона.

Решает не длина строки и не наличие букв, а ЦИФРЫ: номер — это десять цифр
(или одиннадцать с ведущей единицей), всё остальное телефоном не является.
Такое правило отбрасывает текст само собой — в имени цифр нет вовсе.
"""
from __future__ import annotations

import re
from typing import Optional

DIGITS_RE = re.compile(r"\D")

# Международный формат: от восьми до пятнадцати цифр по E.164.
MIN_INTL, MAX_INTL = 8, 15


def normalize(raw: object) -> Optional[str]:
    """Телефон в виде «(718) 715-2475» или None, если это не телефон.

    None означает «показывать нечего» — вызывающий не должен печатать
    значок телефона с пустотой или мусором.
    """
    s = str(raw or "").strip()
    if not s:
        return None

    digits = DIGITS_RE.sub("", s)
    # Ведущая единица — код США, в показе она лишняя.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

    # Явно международный: плюс в начале и правдоподобная длина.
    if s.startswith("+") and MIN_INTL <= len(digits) <= MAX_INTL:
        return "+" + digits

    return None


def looks_like_phone(raw: object) -> bool:
    return normalize(raw) is not None
