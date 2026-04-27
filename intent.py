"""Free-form yes/no intent parser for the weekly passenger check.

Returns 'yes', 'no', or 'unclear'. Caller handles 'unclear' by re-asking
with the buttons rather than guessing — this matters because the side-effect
of a wrong 'no' is wiping a driver's passenger list.

Why phrase patterns are checked before token-level matches:
the message "не актуально" contains the positive substring "актуально", so
without negation phrases checked first it would resolve to 'yes'.

Why token-level matching is gated to ≤2 tokens:
"Нету кнопок ниже" is a complaint about the keyboard, not an answer — its
first word is "нету", which would otherwise be classified as 'no' and clear
the user's list. Restricting token-level checks to short replies avoids this.
"""
from __future__ import annotations

import re
from typing import Literal

Intent = Literal["yes", "no", "unclear"]


_NEG_PHRASES = [
    r"\bне\s*актуально\b", r"\bнеактуально\b",
    r"\bне\s*верно\b", r"\bневерно\b",
    r"\bне\s*правильно\b", r"\bнеправильно\b",
    r"(?<!не\s)\bочистит", r"(?<!не\s)\bудалит",
    r"(?<!не\s)\bсброс", r"(?<!не\s)\bобнул",
    r"\bне\s*вожу\b", r"\bне\s*возит",
    r"\bбольше\s*не\s*вожу", r"\bбольше\s*не\s*возит",
    r"\bпоменялись\b", r"\bпоменялся\b", r"\bизменилось\b",
    r"\bне\s*хочу\b", r"\bне\s*надо\b", r"\bне\s*нужн",
    r"\bnot\s+correct\b", r"\bclear\b", r"\breset\b", r"\bdelete\b",
    r"\bno\s+longer\b",
]

_POS_PHRASES = [
    r"\bактуально\b", r"\bактуальн[ао]\b",
    r"\bбез\s*изменени", r"\bничего\s*не\s*менял", r"\bничего\s*не\s*изменил",
    r"\bвсе\s+(же\s+)?да\b", r"\bвсе\s+верно\b", r"\bвсе\s+правильно\b",
    r"\bвсе\s+ок\b", r"\bвсе\s+норм", r"\bвсе\s+хорошо\b",
    r"\bтак\s*и\s*оставит", r"\bоставить\s*как\s*есть\b", r"\bоставь\s*как\s*есть\b",
    r"\bкак\s*и\s*было\b", r"\bто\s*же\s*самое\b",
    r"\bпока\s+да\b",
    r"\ball\s+good\b", r"\bsame\b", r"\bno\s+changes?\b",
    r"\bcorrect\b", r"\bstill\s+(the\s+)?same\b",
]

_NEG_WORDS = [
    r"нет[ау]?", r"не+", r"неа+", r"no+", r"n", r"nope+",
    r"-+", r"—", r"👎", r"❌",
]

_POS_WORDS = [
    r"да+", r"дa+", r"yes+", r"y", r"yep+", r"yeah+", r"yup+", r"ye+",
    r"ага+", r"угу+", r"окей+", r"ok+", r"ок+", r"о+к+",
    r"верно", r"точно", r"конечно", r"есть", r"sure",
    r"\++", r"👍", r"✅", r"☑",
]


def _normalize(text: str) -> str:
    s = text.strip().lower().replace("ё", "е")
    s = re.sub(r"[!?\.,;:\"'\(\)\[\]/\\]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_yes_no_intent(text: str) -> Intent:
    if not text:
        return "unclear"
    s = _normalize(text)
    if not s:
        return "unclear"

    for p in _NEG_PHRASES:
        if re.search(p, s):
            return "no"
    for p in _POS_PHRASES:
        if re.search(p, s):
            return "yes"

    tokens = s.split()
    if len(tokens) <= 2:
        yes_hits = sum(
            1 for tok in tokens if any(re.fullmatch(p, tok) for p in _POS_WORDS)
        )
        no_hits = sum(
            1 for tok in tokens if any(re.fullmatch(p, tok) for p in _NEG_WORDS)
        )
        if yes_hits and not no_hits:
            return "yes"
        if no_hits and not yes_hits:
            return "no"

    return "unclear"
