"""Acceptance test 14 (spec 005 5.13): importing `petasos.serve`, `petasos.limits`,
and `petasos.sessions.maintenance` has no side effects. Runs in a subprocess in an
empty directory with `os.environ` replaced by a mapping that records reads and
`socket.socket` patched to raise, so a stray read or a stray file or socket would
be caught.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = """
import os
import socket
import sys

_WATCHED = ("PETASOS_DB", "PETASOS_ARCHIVE_DIR", "PETASOS_HOST", "PETASOS_PORT")

class RecordingEnviron(dict):
    def __getitem__(self, key):
        if key in _WATCHED:
            sys.stderr.write(f"READ:{key}\\n")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in _WATCHED:
            sys.stderr.write(f"READ:{key}\\n")
        return super().get(key, default)

os.environ = RecordingEnviron({"PETASOS_DB": "/marker/should/never/be/read.sqlite"})

def _raise_init(self, *a, **k):
    raise AssertionError("a socket was opened at import time")

socket.socket.__init__ = _raise_init

before = set(os.listdir("."))

import petasos.serve  # noqa
import petasos.limits  # noqa
import petasos.sessions.maintenance  # noqa

after = set(os.listdir("."))
assert after == before, f"a file appeared: {after - before}"
print("ok")
"""


def test_importing_the_three_modules_has_no_side_effects(tmp_path: Path) -> None:
    script_path = tmp_path / "check_purity.py"
    script_path.write_text(_SCRIPT)

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert "READ:" not in result.stderr


def test_migrate_creates_no_new_table_for_this_change(tmp_path: Path) -> None:
    from petasos.storage import Database

    db_path = tmp_path / "p.sqlite"
    db = Database(db_path)
    db.migrate()

    conn = db.connect()
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()

    assert "maintenance" not in tables
    before = set(tables)

    db.migrate()
    conn = db.connect()
    try:
        after = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()
    assert after == before
