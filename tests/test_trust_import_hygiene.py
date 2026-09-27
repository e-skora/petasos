"""Importing petasos.trust, petasos.ledger, and petasos.storage touches no network, no
environment variable, and no file outside a temporary directory (acceptance test 38,
the import half).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_importing_the_trust_core_has_no_import_time_side_effects(tmp_path: Path) -> None:
    script = (
        "import os\n"
        "before = sorted(os.listdir('.'))\n"
        "import petasos.storage\n"
        "import petasos.ledger\n"
        "import petasos.trust\n"
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
