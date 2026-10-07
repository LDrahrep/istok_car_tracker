"""Импорт людей, объектов и присутствия из Google Sheets в Postgres.

Направление одностороннее: Sheets → БД, только чтение. Это соответствует
правилу «у каждой сущности ровно один владелец»: справочник сотрудников и
табели пока ведёт HR в таблице, поэтому БД их импортирует и не трогает.

Почему объект берётся из присутствия, а не из отдельной колонки: колонку
пришлось бы заполнять руками на каждого человека и поддерживать при каждом
переезде. Табели и так заполняются — значит объект уже known, его надо
только прочитать.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

# Суффикс в имени листа-табеля → канонический объект. Опечатки реальны:
# в таблице встречались и MELTEH, и MILTECH.
SITE_ALIASES = {
    "AMAZON": "AMAZON", "AMZN": "AMAZON",
    "MELTECH": "MELTECH", "MELTEH": "MELTECH", "MILTECH": "MELTECH", "MLT": "MELTECH",
    "COLUMBUS": "COLUMBUS", "CLMB": "COLUMBUS", "CBUS": "COLUMBUS",
    "BUFFALO": "BUFFALO", "BUFF": "BUFFALO", "BUF": "BUFFALO", "BFLO": "BUFFALO",
}
def _site_re(aliases: dict[str, str], anchored: bool) -> re.Pattern:
    """Регексп поиска объекта по списку псевдонимов.

    Строится из переданного словаря, а не из констант: справочник объектов
    живёт в таблице `site`, иначе новый объект — это правка кода и деплой.
    Длинные псевдонимы идут первыми, чтобы «FLUIDSTACK TX» не схлопнулся
    в «FLUIDSTACK».
    """
    body = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    tail = r")\s*$" if anchored else r")\b"
    return re.compile(r"\b(" + body + tail, re.I)


SITE_SUFFIX_RE = _site_re(SITE_ALIASES, anchored=True)
# Диапазон недели в имени листа. Разделители внутри дат бывают любые
# («09142026-09202026», «8/17/2026-8/23/2026»), поэтому ловим весь кусок
# целиком и разбираем его цифрами, а не группами регекспа — см. _split_range.
WEEK_RANGE_RE = re.compile(r"\d[\d/.\-]{7,24}\d")

# Листы, которые заканчиваются на название объекта, но табелями не являются.
NOT_TIMESHEETS = {"drivers", "drivers_passengers", "employees",
                  "svodka columbus", "svodka buffalo", "svodka amazon"}


@dataclass
class PresenceRow:
    name: str
    work_date: date
    site_id: str


def name_key(raw: str) -> str:
    """Ключ сопоставления: отсортированные токены в нижнем регистре.

    Берёт на себя главную беду этих данных — перестановку имени и фамилии
    («Khushmamadov Khushmamad» против «Khushmamad Khushmamadov») и разницу
    регистра (9 человек записаны капсом). NFKC убирает невидимые символы,
    которых в выгрузках хватает.
    """
    s = unicodedata.normalize("NFKC", raw or "")
    s = s.replace(" ", " ").replace("​", "").replace("﻿", "")
    tokens = [t for t in s.casefold().split() if t]
    return " ".join(sorted(tokens))


def split_shift_and_site(raw_shift: str) -> tuple[str, Optional[str]]:
    """«Meltech Day» → ('day', 'MELTECH'), «Night» → ('night', None).

    Объект сидел внутри значения смены — из-за этого Meltech был случайно
    отделён от Amazon, а Columbus и Buffalo не были отделены ничем.
    Здесь это разводится на два независимых поля.
    """
    s = (raw_shift or "").replace(" ", " ").strip().casefold()
    if not s:
        return "unknown", None
    site = None
    if "meltech" in s or "meltch" in s:
        site = "MELTECH"
        s = s.replace("meltech", "").replace("meltch", "").strip()
    if "night" in s:
        return "night", site
    if "day" in s:
        return "day", site
    # Голый «Meltech» исторически означал дневную смену.
    return ("day", site) if site else ("unknown", None)


def site_from_sheet_name(sheet_name: str,
                        aliases: Optional[dict[str, str]] = None) -> Optional[str]:
    """Объект из имени листа.

    Сначала ищем в конце — так названы листы основной таблицы
    («09/14/2026-09/20/2026 AMAZON»). Если не нашли, ищем где угодно: в
    документах, которые ведут подрядчики, объект стоит в начале
    («Tulane 9/28/2026-10/4/2026»). Суффикс проверяется первым, чтобы
    поведение на уже работающих именах не изменилось ни на шаг.
    """
    name = (sheet_name or "").strip()
    if name.casefold() in NOT_TIMESHEETS:
        return None
    table = aliases or SITE_ALIASES
    for anchored in (True, False):
        pattern = (SITE_SUFFIX_RE if anchored and table is SITE_ALIASES
                   else _site_re(table, anchored))
        m = pattern.search(name)
        if m:
            return table[m.group(1).upper()]
    return None


def _month_day_candidates(token: str) -> list[tuple[int, int, int]]:
    """Разборы токена вида MDDYYYY/MMDDYYYY в порядке предпочтения.

    Формат неоднозначен: «8242026» — это и 8/24/2026, и 82/4/2026. Регекспу
    такое решать нельзя, он отдаёт первый разбор по жадности и на реальном
    имени листа выбирал месяц 82. Поэтому варианты перечисляются явно, а
    отбор идёт по валидности даты.

    Порядок предпочтения — «день записан двумя цифрами». Он взят из живых
    имён: «09142026» padding и у месяца, и у дня, а «8242026» теряет ноль
    только у месяца. Значит три цифры до года читаются как M + DD.
    """
    if not 6 <= len(token) <= 8:
        return []
    year, rest = int(token[-4:]), token[:-4]
    if len(rest) == 2:
        splits = [(rest[0], rest[1])]
    elif len(rest) == 3:
        splits = [(rest[0], rest[1:]), (rest[:2], rest[2])]
    elif len(rest) == 4:
        splits = [(rest[:2], rest[2:])]
    else:
        return []
    return [(year, int(m), int(d)) for m, d in splits]


def _valid(parts: tuple[int, int, int]) -> Optional[date]:
    try:
        return date(*parts)
    except ValueError:
        return None


def _split_range(digits: str) -> list[tuple[str, str]]:
    """Делит цифры диапазона на начальный и конечный токен.

    Обе даты записаны одинаково, поэтому основной вариант — ровно посередине.
    Неравные половины («8242026-09202026») встречаются, поэтому рядом
    пробуются сдвиги на одну цифру.
    """
    n = len(digits)
    cuts = []
    if n % 2 == 0:
        cuts.append(n // 2)
    for cut in (n - 6, n - 7, n - 8):
        if 6 <= cut <= 8 and cut not in cuts:
            cuts.append(cut)
    return [(digits[:c], digits[c:]) for c in cuts]


def week_dates_from_name(sheet_name: str) -> Optional[list[date]]:
    """Семь дат из имени листа вида 09142026-09202026.

    Имя приоритетнее строки 1: шапки часто копируют с чужой недели, и тогда
    даты в них врут. Конечная дата диапазона используется как проверка —
    если она ровно на шесть дней позже начальной, разбор почти наверняка
    верный; это снимает часть неоднозначности формата.
    """
    m = WEEK_RANGE_RE.search(str(sheet_name or ""))
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))

    fallback: Optional[date] = None
    for start_tok, end_tok in _split_range(digits):
        starts = [d for d in map(_valid, _month_day_candidates(start_tok)) if d]
        ends = [d for d in map(_valid, _month_day_candidates(end_tok)) if d]
        for start in starts:
            if any((end - start).days == 6 for end in ends):
                return [start + timedelta(days=i) for i in range(7)]
        if fallback is None and starts:
            fallback = starts[0]

    if fallback is None:
        return None
    return [fallback + timedelta(days=i) for i in range(7)]


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(value or "").strip())
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def parse_timesheet(values: Sequence[Sequence[object]], sheet_name: str,
                    aliases: Optional[dict[str, str]] = None) -> list[PresenceRow]:
    """Табель → список фактов присутствия.

    Геометрия жёсткая и такая же, как в GAS: даты — строка 1, колонки C..I;
    имена — колонка B; данные начинаются со строки 3; присутствием считается
    любая непустая ячейка (часы, крестик, инициалы — значение не разбирается).
    """
    site = site_from_sheet_name(sheet_name, aliases)
    if not site or len(values) < 3:
        return []

    dates = week_dates_from_name(sheet_name)
    if dates is None:
        header = values[0]
        dates = [_as_date(header[2 + i]) if 2 + i < len(header) else None
                 for i in range(7)]
    if not any(dates):
        return []

    out: list[PresenceRow] = []
    for row in values[2:]:
        name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        if not name:
            continue
        for i in range(7):
            day = dates[i] if i < len(dates) else None
            cell = row[2 + i] if 2 + i < len(row) else None
            if day and cell not in ("", None):
                out.append(PresenceRow(name=name, work_date=day, site_id=site))
    return out


def latest_site_by_person(rows: Iterable[PresenceRow]) -> dict[str, str]:
    """Текущий объект = объект самой свежей отметки.

    Отсюда и берётся current_site_id: вести его руками не нужно, переезд
    человека виден из табеля на следующий же день.
    """
    best: dict[str, tuple[date, str]] = {}
    for r in rows:
        key = name_key(r.name)
        prev = best.get(key)
        if prev is None or r.work_date > prev[0]:
            best[key] = (r.work_date, r.site_id)
    return {k: v[1] for k, v in best.items()}


# ──────────────────────── сборка ростера (чистая часть) ────────────────────────

@dataclass
class PersonRow:
    full_name: str
    name_key: str
    shift: str
    telegram_id: Optional[int]
    current_site_id: Optional[str]


def build_people(
    employees: Iterable[tuple[str, str]],
    driver_tgids: dict[str, int],
    presence: Iterable[PresenceRow],
) -> list[PersonRow]:
    """Ростер из трёх источников, склеенный по name_key.

    `employees` — пары (имя, сырая смена) из листа employees: это канонический
    справочник HR, поэтому его написание имени побеждает.
    `driver_tgids` — name_key → собственный telegram_id, ТОЛЬКО из листа
    drivers. Брать его из employees нельзя: там колонка DriverTGID означает
    «ID водителя, с которым едет сотрудник», и такой импорт присвоил бы
    пассажирам ID их водителя (см. models.Driver/Employee).
    `presence` — факты из табелей; дают людей, которых в employees нет, и
    текущий объект.

    Объединение, а не пересечение: имя, которое есть в табеле и отсутствует в
    employees, — это реальная рассинхронизация, и её надо видеть в админке,
    а не терять на импорте.
    """
    presence = list(presence)
    latest_site = latest_site_by_person(presence)
    by_key: dict[str, PersonRow] = {}

    def merge(full_name: str, shift: str, site: Optional[str]) -> None:
        key = name_key(full_name)
        if not key:
            return
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = PersonRow(
                full_name=full_name.strip(), name_key=key, shift=shift,
                telegram_id=driver_tgids.get(key),
                current_site_id=latest_site.get(key) or site,
            )
            return
        # Первое написание (из employees) не перезаписываем: HR-справочник
        # пишет имена аккуратнее, чем табели.
        if cur.shift == "unknown" and shift != "unknown":
            cur.shift = shift
        if cur.current_site_id is None:
            cur.current_site_id = latest_site.get(key) or site

    for raw_name, raw_shift in employees:
        shift, site = split_shift_and_site(raw_shift)
        merge(raw_name, shift, site)

    for row in presence:
        merge(row.name, "unknown", row.site_id)

    return list(by_key.values())


def resolve_telegram_conflicts(
    people: list[PersonRow],
) -> tuple[list[PersonRow], list[tuple[str, str, int]]]:
    """Один telegram_id не может принадлежать двум людям — так объявлен UNIQUE.

    Живой случай: водителя переименовали, в листе drivers осталась старая
    строка, и один и тот же ID претендует на два name_key. Без разрешения
    конфликта вся вставка падает целиком, причём на середине пачки.
    Оставляем ID первому, у остальных снимаем и возвращаем список, чтобы
    администратор увидел пару имён и починил источник.
    """
    seen: dict[int, str] = {}
    conflicts: list[tuple[str, str, int]] = []
    for p in people:
        if p.telegram_id is None:
            continue
        owner = seen.get(p.telegram_id)
        if owner is None:
            seen[p.telegram_id] = p.name_key
        else:
            conflicts.append((owner, p.name_key, p.telegram_id))
            p.telegram_id = None
    return people, conflicts


def sites_referenced(
    people: Iterable[PersonRow], presence: Iterable[PresenceRow]
) -> list[str]:
    """Все объекты, на которые кто-то ссылается.

    Нужны раньше person и presence: там обе колонки — REFERENCES site(id),
    и вставка без справочника объектов упала бы по внешнему ключу.
    """
    ids = {p.current_site_id for p in people if p.current_site_id}
    ids |= {r.site_id for r in presence if r.site_id}
    return sorted(ids)


def is_timesheet(sheet_name: str,
                 aliases: Optional[dict[str, str]] = None) -> bool:
    """Лист-табель = опознанный объект И разбираемый диапазон дат в имени.

    Второе условие отсеивает листы вроде «PHASE 5 AMAZON»: объект в имени
    есть, а к какой неделе относятся колонки — неизвестно.
    """
    return (site_from_sheet_name(sheet_name, aliases) is not None
            and week_dates_from_name(sheet_name) is not None)


# ──────────────────── связь «кто кого вёз» (чистая часть) ────────────────────

@dataclass
class CarpoolLink:
    ride_date: date
    driver_id: int
    passenger_id: int
    seat: int


def build_carpool(
    snapshots: Iterable[tuple],
    person_by_key: dict[str, int],
    person_by_tgid: dict[int, int],
) -> tuple[list[CarpoolLink], dict[str, list]]:
    """Снапшоты (сырой слой) → связи между людьми (нормализованный слой).

    `snapshots` — кортежи (дата, telegram_id водителя, имя водителя, список
    имён пассажиров) прямо из `carpool_snapshot`.

    Разрешать имена здесь, а не в запросах потом, — весь смысл таблицы:
    нечёткое сопоставление делается один раз, а не заново при каждом вопросе
    «а был ли он в тот день на объекте».

    Всё, что не разрешилось, не выбрасывается молча, а возвращается во
    второй половине ответа: невидимая потеря строк на импорте — ровно та
    беда, из-за которой всё это и затевалось.
    """
    rows: list[CarpoolLink] = []
    bad: dict[str, list] = {"driver": [], "passenger": [], "self": [], "taken": []}
    # (дата, пассажир) → водитель. Второй водитель на ту же пару нарушил бы
    # первичный ключ и уронил всю пачку, поэтому отсекаем здесь и сообщаем.
    seen: dict[tuple[date, int], int] = {}

    for ride_date, tg_id, driver_name, passengers in sorted(
        snapshots, key=lambda s: (s[0], s[1])
    ):
        driver_id = None
        if tg_id is not None:
            driver_id = person_by_tgid.get(int(tg_id))
        if driver_id is None:
            driver_id = person_by_key.get(name_key(driver_name or ""))
        if driver_id is None:
            bad["driver"].append((ride_date, driver_name))
            continue

        seat = 0
        for raw in passengers or []:
            pid = person_by_key.get(name_key(raw))
            if pid is None:
                bad["passenger"].append((ride_date, raw))
                continue
            if pid == driver_id:
                bad["self"].append((ride_date, driver_name))
                continue
            key = (ride_date, pid)
            if key in seen:
                bad["taken"].append((ride_date, raw, seen[key], driver_id))
                continue
            seat += 1
            if seat > 4:
                bad["taken"].append((ride_date, raw, driver_id, None))
                continue
            seen[key] = driver_id
            rows.append(CarpoolLink(ride_date, driver_id, pid, seat))

    return rows, bad


def suspicious_sheets(titles: Iterable[str],
                      aliases: Optional[dict[str, str]] = None) -> list[tuple[str, str]]:
    """Листы, похожие на табель, но не прошедшие отбор, — с причиной.

    Молчаливый пропуск здесь дорого стоит: если табель за неделю назван
    непривычно, присутствие за эту неделю просто не появится, и доплаты
    по ней посчитать будет нечем. Отличить «лист не табель» от «табель,
    который я не узнал» можно только назвав причину.

    Листы, где нет ни признака объекта, ни диапазона дат, не возвращаются:
    это заведомо не табели (employees, drivers, Svodka).
    """
    out: list[tuple[str, str]] = []
    for title in titles:
        if is_timesheet(title, aliases):
            continue
        site = site_from_sheet_name(title, aliases)
        dates = week_dates_from_name(title)
        if site is None and dates is None:
            continue
        reason = "объект в имени не опознан" if site is None else "не разобран диапазон дат"
        out.append((title, reason))
    return out
