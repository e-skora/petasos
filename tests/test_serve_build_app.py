"""Acceptance test 2 (spec 005 5.1): `build_app` creates the database and archive
folders, migrates, and wraps the composed app in `MaintenanceRunner` and
`RequestSizeLimit`; `--check` builds without binding a port; `main()` calls
`uvicorn.run` with the pinned keyword arguments.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from starlette.testclient import TestClient
from support_005 import lifespan_harness

from petasos import serve
from petasos.sessions.maintenance import MaintenanceRunner

if TYPE_CHECKING:
    from conftest import FrozenClock

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_build_app_creates_folders_migrates_and_wraps(
    tmp_path: Path, frozen_clock: FrozenClock
) -> None:
    db_path = tmp_path / "nested" / "p.sqlite"
    archive_dir = tmp_path / "nested" / "archive"
    settings = serve.Settings(db_path=db_path, archive_dir=archive_dir, host="0.0.0.0", port=8080)

    app = serve.build_app(settings, clock=frozen_clock)

    assert db_path.parent.is_dir()
    assert archive_dir.is_dir()
    assert isinstance(app.app, MaintenanceRunner)

    with TestClient(app) as client:
        response = client.post("/session")
    assert response.status_code == 201


async def test_build_app_lifespan_runs_a_tick_and_answers_requests(
    tmp_path: Path, frozen_clock: FrozenClock
) -> None:
    settings = serve.Settings(
        db_path=tmp_path / "p.sqlite", archive_dir=tmp_path / "archive", host="0.0.0.0", port=8080
    )
    app = serve.build_app(settings, clock=frozen_clock)
    async with lifespan_harness(app):
        import httpx2

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/healthz")
        assert response.status_code == 200


def test_check_flag_builds_migrates_prints_and_binds_no_port(tmp_path: Path) -> None:
    db_path = tmp_path / "p.sqlite"
    result = subprocess.run(
        [sys.executable, "-m", "petasos.serve", "--check"],
        cwd=REPO_ROOT,
        env={
            "PETASOS_DB": str(db_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"check ok {db_path}"
    assert db_path.exists()


def test_main_calls_uvicorn_run_once_with_the_pinned_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "p.sqlite"
    monkeypatch.setenv("PETASOS_DB", str(db_path))
    monkeypatch.setenv("PETASOS_HOST", "127.0.0.1")
    monkeypatch.setenv("PETASOS_PORT", "9001")

    calls: list[dict] = []

    def fake_run(app, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)

    exit_code = serve.main([])

    assert exit_code == 0
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["workers"] == 1
    assert kwargs["timeout_graceful_shutdown"] == 30
    assert kwargs["timeout_keep_alive"] == 65
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9001
