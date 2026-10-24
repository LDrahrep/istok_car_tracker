"""Временный Postgres для тестов, которым нужен настоящий SQL.

Фейковый курсор проверяет только число подстановок, а не то, что считается.
Поэтому такие тесты поднимают кластер initdb во временной папке и накатывают
на него schema.sql. Нет локального Postgres или psycopg — тесты с фикстурой
`pg` пропускаются, остальной набор от них не зависит.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pg_bin(name: str):
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/opt/homebrew/opt/postgresql@16/bin/{name}",
                      f"/usr/local/opt/postgresql@16/bin/{name}"):
        if os.path.exists(candidate):
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg(tmp_path_factory):
    psycopg = pytest.importorskip("psycopg")
    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("нет локального Postgres")

    base = tmp_path_factory.mktemp("pg")
    data = base / "data"
    subprocess.run([initdb, "-D", str(data), "-A", "trust", "-U", "test", "--no-sync"],
                   check=True, capture_output=True)
    port = _free_port()
    # Unix-сокет выключен: путь к временной папке на macOS длиннее лимита.
    opts = (f"-p {port} -c listen_addresses=127.0.0.1 "
            "-c unix_socket_directories='' -c fsync=off")
    subprocess.run([pg_ctl, "-D", str(data), "-o", opts, "-l", str(base / "log"),
                    "-w", "start"], check=True, capture_output=True)
    dsn = f"postgresql://test@127.0.0.1:{port}/postgres"
    schema = (Path(ROOT) / "db" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema)
    yield dsn
    subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "stop"],
                   capture_output=True)
