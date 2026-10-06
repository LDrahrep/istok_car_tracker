"""Статическая проверка обращений к self.

Python разрешает атрибуты во время выполнения, поэтому `self._sheet()`
в методе, который никто ещё не вызывал, выглядит как рабочий код: импорт
проходит, py_compile молчит, тесты зелёные. Так и случилось — метод
sheet_titles добавили «на будущее», а первым его вызвал /db_import
в продакшене и упал на 'SheetManager' object has no attribute '_sheet'.

Проверка разбирает исходник и сверяет каждое self.X с тем, что класс
действительно определяет. Это третий случай той же природы за день:
до него были выдуманные ключи локалей и выдуманные коды причин, и оба
лечились такой же проверкой исходника.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent

# Атрибуты, которые класс получает извне: их подставляют при создании
# или в тестах, в теле класса присваивания нет.
INJECTED = {
    "BotHandlers": {"sheets", "config", "application"},
    "SheetManager": set(),
}


def _class_nodes():
    for name in ("sheets.py", "handlers.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                yield name, node


def _defined(cls: ast.ClassDef) -> set[str]:
    """Всё, что класс объявляет: методы, поля класса и self.X = ... внутри."""
    out = set(INJECTED.get(cls.name, ()))
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    for node in ast.walk(cls):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if (isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name) and t.value.id == "self"):
                    out.add(t.attr)
    return out


def _used(cls: ast.ClassDef) -> set[str]:
    return {
        node.attr for node in ast.walk(cls)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == "self"
    }


def test_every_self_attribute_exists():
    problems = []
    for file_name, cls in _class_nodes():
        missing = sorted(_used(cls) - _defined(cls))
        if missing:
            problems.append(f"{file_name}:{cls.name} → {missing}")
    assert not problems, "обращение к несуществующим атрибутам: " + "; ".join(problems)
