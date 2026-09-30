"""petasos.memory never imports petasos.trust, MemoryTier is a distinct total order,
and importing the package or constructing its store has no import-time or
construction-time side effects (spec 002-memory-canary-guard 2.1, 2.15; acceptance
tests 1 and 2).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from petasos.memory import MemoryStore, MemoryTier
from petasos.storage import Database


def test_memory_tier_is_totally_ordered_and_distinct_from_trust_tier() -> None:
    from petasos.trust import Tier

    assert MemoryTier is not Tier
    assert MemoryTier.T0 < MemoryTier.T5

    ordered = list(MemoryTier)
    assert ordered == sorted(ordered)
    for i, lo in enumerate(ordered):
        for hi in ordered[i + 1 :]:
            assert lo < hi
            assert hi > lo
            assert lo <= hi
            assert hi >= lo


def test_petasos_memory_does_not_import_petasos_trust_in_a_fresh_process() -> None:
    script = (
        "import sys\n"
        "import petasos.memory\n"
        "assert 'petasos.trust' not in sys.modules, sorted(sys.modules)\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(sys.path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_importing_petasos_memory_has_no_import_time_side_effects(tmp_path: Path) -> None:
    script = (
        "import os\n"
        "before = sorted(os.listdir('.'))\n"
        "import petasos.memory\n"
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


def test_constructing_memory_store_creates_no_file(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    MemoryStore(db, scope="s1", clock=frozen_clock)
    assert not tmp_sqlite.exists()


def test_migrate_creates_memory_tables_and_is_idempotent(tmp_sqlite: Path) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    db.migrate()
    with db.transaction() as conn:
        names = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"memory_entries", "canaries"} <= names
