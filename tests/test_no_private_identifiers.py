"""Fails if any denylisted private-project identifier appears anywhere in this repository.

The denylist itself is private and is never committed here. It is supplied at CI time as the
PRIVATE_DENYLIST secret, one entry per line, with blank lines and lines starting with "#"
ignored. Locally, with no PRIVATE_DENYLIST set, this test skips with a visible warning: see
AGENTS.md and DECISIONS.md D-008 for why the gate is structured this way.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SKIP_DIR_NAMES = {".git", ".venv", "node_modules", "_private", "archive"}
SKIP_RELATIVE_DIRS = {"demo/dist", "site/demo"}


def _load_denylist() -> list[str]:
    raw = os.environ.get("PRIVATE_DENYLIST", "")
    entries = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(stripped)
    return entries


def _is_skipped_dir(relative_dir: Path) -> bool:
    if any(part in SKIP_DIR_NAMES for part in relative_dir.parts):
        return True
    relative_str = relative_dir.as_posix()
    return any(
        relative_str == skip or relative_str.startswith(skip + "/") for skip in SKIP_RELATIVE_DIRS
    )


def _iter_text_files(root: Path, self_path: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        relative_dir = current.relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not _is_skipped_dir(relative_dir / d))
        for filename in sorted(filenames):
            file_path = current / filename
            if file_path.resolve() == self_path:
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            yield file_path, text


def test_no_private_identifiers():
    denylist = _load_denylist()
    if not denylist:
        pytest.skip("PRIVATE_DENYLIST not set; the private-identifier gate only runs in CI")

    self_path = Path(__file__).resolve()
    root = self_path.parent.parent

    failures = []
    for file_path, text in _iter_text_files(root, self_path):
        relative = file_path.relative_to(root)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for index, entry in enumerate(denylist):
                if entry in line:
                    failures.append(f"{relative}:{line_number} (denylist entry #{index})")

    assert not failures, "Private identifiers found (file:line, denylist index):\n" + "\n".join(
        failures
    )
