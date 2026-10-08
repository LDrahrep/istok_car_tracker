"""Указатель админских команд должен совпадать с тем, что есть в боте.

Справка, которую забывают обновлять, вреднее отсутствующей: ей верят.
Поэтому список сверяется с регистрацией в bot.py, а не поддерживается
на честном слове.
"""
from __future__ import annotations

import re
from pathlib import Path

import admin_commands

ROOT = Path(__file__).parent

# Команды, доступные всем, — в админском указателе им не место.
PUBLIC = {"start", "help", "english", "russian"}


def _registered() -> set[str]:
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    return set(re.findall(r'CommandHandler\("([a-z_]+)"', src)) - PUBLIC


def test_every_admin_command_is_documented():
    missing = sorted(_registered() - admin_commands.all_names())
    assert not missing, f"команды есть, а описания нет: {missing}"


def test_index_has_no_phantom_commands():
    """Описание несуществующей команды — обещание, которого бот не выполнит."""
    phantom = sorted(admin_commands.all_names() - _registered())
    assert not phantom, f"описаны, но не зарегистрированы: {phantom}"


def test_every_entry_explains_itself():
    for _, cmds in admin_commands.GROUPS:
        for c in cmds:
            assert c.what.strip(), f"/{c.name} без описания"
            assert len(c.what) > 15, f"/{c.name}: описание слишком куцее"


def test_rendered_text_fits_one_telegram_message():
    """4096 — предел Telegram. Обрезанная справка теряет последние группы."""
    assert len(admin_commands.render()) < 4000


def test_no_duplicate_commands_between_groups():
    seen, dupes = set(), []
    for _, cmds in admin_commands.GROUPS:
        for c in cmds:
            if c.name in seen:
                dupes.append(c.name)
            seen.add(c.name)
    assert not dupes, f"команда описана дважды: {dupes}"
