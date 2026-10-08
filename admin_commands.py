"""Описания админских команд — одним списком.

Держится отдельно от обработчиков, чтобы указатель нельзя было забыть
обновить: тест сверяет этот список с тем, что зарегистрировано в bot.py,
и падает, когда появляется команда без описания. Иначе справка устаревает
на второй неделе и ею перестают пользоваться.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cmd:
    name: str
    args: str
    what: str


GROUPS: tuple[tuple[str, tuple[Cmd, ...]], ...] = (
    ("Люди и карпулы", (
        Cmd("whois", "<имя или tg>", "всё про человека: смена, объект, с кем едет, история"),
        Cmd("unlink", "<имя пассажира>", "открепить пассажира от его водителя"),
        Cmd("db_stale", "[дней]", "кто держит пассажиров, не появляясь в табелях"),
        Cmd("db_merge", "Старое Имя | Новое Имя", "склеить две записи одного человека"),
    )),
    ("Данные", (
        Cmd("db_status", "", "состояние захвата: объём, глубина, пропуски"),
        Cmd("db_capture", "", "снимок на сегодня и пересборка связей"),
        Cmd("db_import", "[ростер | подстрока]", "импорт табелей и справочников"),
        Cmd("db_sheets", "", "все листы таблицы с разбором"),
        Cmd("db_diff", "", "сверить состав карпулов в таблице и в базе"),
        Cmd("db_backfill", "<week1..4>", "перенести историю из week-листа"),
        Cmd("db_restore", "[дней] [да]", "вернуть списки, стёртые проверкой"),
    )),
    ("Отчёты", (
        Cmd("db_report", "<с> <по> [табель]", "сводка по доплатам из базы"),
        Cmd("db_export", "[лист]", "выгрузить данные в таблицу"),
        Cmd("report", "", "сводка из листа Svodka (старый путь, GAS)"),
    )),
    ("Объекты", (
        Cmd("db_site_rename", "СТАРЫЙ НОВЫЙ [Имя]", "переименовать объект с сохранением истории"),
    )),
    ("Справка", (
        Cmd("admin", "", "этот список команд с кратким описанием каждой"),
    )),
    ("Рассылки", (
        Cmd("broadcast", "<текст>", "сообщение всем водителям"),
        Cmd("broadcast_keyboard", "", "обновить клавиатуру у всех"),
    )),
)


def all_names() -> set[str]:
    return {c.name for _, cmds in GROUPS for c in cmds}


def version() -> str:
    """Что за код сейчас работает.

    Railway помечает деплой SUCCESS в момент создания, а не когда
    контейнер начал отвечать. Дважды за сутки это привело к неверному
    выводу: проверка шла по старому контейнеру, а выглядела как
    проверка нового. Признак из самого приложения снимает весь класс
    таких ошибок.
    """
    import os
    import subprocess

    sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")
    if not sha:
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                 text=True, timeout=3).stdout.strip()
        except Exception:  # noqa: BLE001
            sha = ""
    msg = os.getenv("RAILWAY_GIT_COMMIT_MESSAGE", "")
    started = os.getenv("RAILWAY_DEPLOYMENT_ID", "")
    parts = [f"Версия: {sha[:7] or 'неизвестна'}"]
    if msg:
        parts.append(f"«{msg.splitlines()[0][:60]}»")
    if started:
        parts.append(f"деплой {started[:8]}")
    return " · ".join(parts)


def render() -> str:
    lines = ["🛠 Админские команды", version(), ""]
    for title, cmds in GROUPS:
        lines.append(f"▸ {title}")
        for c in cmds:
            head = f"/{c.name}" + (f" {c.args}" if c.args else "")
            lines.append(f"  {head}")
            lines.append(f"     {c.what}")
        lines.append("")
    lines.append("Без аргументов большинство команд показывает подсказку "
                 "и ничего не меняет.")
    return "\n".join(lines)
