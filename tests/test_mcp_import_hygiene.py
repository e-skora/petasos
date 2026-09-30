"""Importing `petasos.mcp`, `petasos.helpdesk`, and `petasos.sessions` touches no
file, environment variable, or network; `Database.migrate()` creates every table
this change adds, and twice is a no-op (spec 3.26; acceptance test 28)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_importing_this_changes_packages_has_no_import_time_side_effects(tmp_path: Path) -> None:
    script = (
        "import os\n"
        "before = sorted(os.listdir('.'))\n"
        "import petasos.storage\n"
        "import petasos.mcp\n"
        "import petasos.helpdesk\n"
        "import petasos.sessions\n"
        "after = sorted(os.listdir('.'))\n"
        "assert before == after, (before, after)\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(sys.path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
    assert list(tmp_path.iterdir()) == []


def test_migrate_creates_every_table_this_change_adds_and_is_idempotent(tmp_path: Path) -> None:
    from petasos.storage import Database

    db = Database(tmp_path / "t.sqlite")
    db.migrate()
    db.migrate()

    conn = db.connect()
    try:
        names = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {
        "tickets",
        "notes",
        "helpdesk_fake_mail",
        "helpdesk_fake_refunds",
        "sessions",
        "identities",
        "quota_events",
        "abuse_counters",
    } <= names
