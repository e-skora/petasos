"""Acceptance tests 12 and 13 (spec 005): the Dockerfile, `.dockerignore`,
`fly.toml`, and the two docs are exactly what spec 5.2, 5.3, 5.11, and 5.12
require. Every check reads the files on disk; none opens the network or builds an
image.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_DOCKERFILE_DOTIGNORE_ENTRIES = (
    ".git",
    ".venv",
    "**/__pycache__",
    "tests",
    "demo",
    "site",
    "docs",
    "review",
    "changes",
    "scripts",
    "*.sqlite*",
    ".github",
)


def test_dockerfile_copies_readme_before_the_first_sync() -> None:
    text = (REPO_ROOT / "Dockerfile").read_text()
    lines = text.splitlines()
    copy_line_index = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("COPY") and "pyproject.toml" in line and "uv.lock" in line
    )
    copy_line = lines[copy_line_index]
    assert "README.md" in copy_line
    sync_line_index = next(i for i, line in enumerate(lines) if "uv sync" in line)
    assert sync_line_index > copy_line_index


def test_dockerfile_pins_the_uv_image_tag() -> None:
    text = (REPO_ROOT / "Dockerfile").read_text()
    assert "ghcr.io/astral-sh/uv:0.12.21" in text


def test_dockerfile_puts_the_venv_first_on_path() -> None:
    text = (REPO_ROOT / "Dockerfile").read_text()
    assert 'PATH="/app/.venv/bin:$PATH"' in text


def test_dockerfile_cmd_is_the_serve_entrypoint_with_no_factory_flag() -> None:
    text = (REPO_ROOT / "Dockerfile").read_text()
    assert 'CMD ["python", "-m", "petasos.serve"]' in text
    assert "--factory" not in text


def test_dockerignore_lists_every_entry() -> None:
    lines = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    for entry in _DOCKERFILE_DOTIGNORE_ENTRIES:
        assert entry in lines, entry


def test_fly_toml_parses_and_has_every_key() -> None:
    with (REPO_ROOT / "fly.toml").open("rb") as f:
        data = tomllib.load(f)

    assert data["app"] == "petasos-api"
    assert data["primary_region"] == "sjc"
    assert data["kill_timeout"] == 40
    assert isinstance(data["kill_timeout"], int)

    assert data["env"] == {
        "PETASOS_DB": "/data/petasos.sqlite",
        "PETASOS_ARCHIVE_DIR": "/data/archive",
    }
    assert data["mounts"] == {"source": "petasos_data", "destination": "/data"}

    http_service = data["http_service"]
    assert http_service["internal_port"] == 8080
    assert http_service["force_https"] is True
    assert http_service["auto_stop_machines"] == "off"
    assert http_service["auto_start_machines"] is False
    assert http_service["min_machines_running"] == 1

    concurrency = http_service["concurrency"]
    assert concurrency["type"] == "requests"
    assert concurrency["soft_limit"] == 200
    assert concurrency["hard_limit"] == 400

    checks = http_service["checks"]
    assert len(checks) == 1
    check = checks[0]
    assert check["method"] == "GET"
    assert check["path"] == "/healthz"
    assert check["protocol"] == "http"
    assert check["interval"] == "15s"
    assert check["timeout"] == "2s"
    assert check["grace_period"] == "5s"

    vm = data["vm"]
    assert len(vm) == 1
    assert vm[0]["size"] == "shared-cpu-1x"
    assert vm[0]["memory"] == "256mb"


_REQUIRED_DEPLOY_PHRASES = (
    "python -m petasos.serve",
    "petasos.sessions.maintenance reset",
    "previous known-good commit",
    "exactly one machine",
    "--ha=false",
    "_fly-ownership",
    "Full (strict)",
    "fly volumes list",
    "fly config validate",
)


def test_deploy_doc_has_no_em_dash_no_factory_flag_and_the_required_phrases() -> None:
    text = (REPO_ROOT / "docs" / "deploy.md").read_text()
    assert "—" not in text
    assert "--factory" not in text
    for phrase in _REQUIRED_DEPLOY_PHRASES:
        assert phrase in text, phrase


_REQUIRED_SETUP_PHRASES = ("POST /session", "python -m petasos.serve", "docs/deploy.md")


def test_setup_doc_has_no_em_dash_no_stale_phrases_and_the_required_phrases() -> None:
    text = (REPO_ROOT / "docs" / "setup.md").read_text()
    assert "—" not in text
    assert "--factory" not in text
    assert "tokens mapping" not in text
    for phrase in _REQUIRED_SETUP_PHRASES:
        assert phrase in text, phrase


def test_each_documented_python_dash_m_command_exits_2_on_an_unknown_argument(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "p.sqlite"
    env = {"PETASOS_DB": str(db_path), "PATH": "/usr/bin:/bin"}
    for module in ("petasos.serve", "petasos.sessions.maintenance"):
        result = subprocess.run(
            [sys.executable, "-m", module, "not-a-real-argument"],
            cwd=REPO_ROOT,
            env={**env, "PYTHONPATH": str(REPO_ROOT / "src")},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 2, (module, result.stdout, result.stderr)
