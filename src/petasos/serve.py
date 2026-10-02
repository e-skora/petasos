"""The entrypoint: settings from the environment, migrate, compose, uvicorn (spec
005 5.1). Environment variables are read in exactly three places in `src/`: this
module's `settings_from_environ`, the maintenance command line (through this same
function), and 003's `app.py` `__main__` block, which stays as it is. `create_app`
itself stays pure (003 spec 3.26).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from petasos.app import create_app
from petasos.limits import RequestSizeLimit
from petasos.sessions import maintenance
from petasos.storage import Database


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: Path
    archive_dir: Path
    host: str
    port: int


def settings_from_environ(environ: Mapping[str, str]) -> Settings:
    """Reads `PETASOS_DB` (required), `PETASOS_ARCHIVE_DIR`, `PETASOS_HOST`, and
    `PETASOS_PORT` from `environ` only; it never touches `os.environ` itself."""
    db = environ.get("PETASOS_DB")
    if not db:
        raise SettingsError("PETASOS_DB is not set; refusing to start without a database path.")
    db_path = Path(db)

    archive_dir_raw = environ.get("PETASOS_ARCHIVE_DIR")
    archive_dir = Path(archive_dir_raw) if archive_dir_raw else db_path.parent / "archive"

    host = environ.get("PETASOS_HOST") or "0.0.0.0"

    port_raw = environ.get("PETASOS_PORT")
    if not port_raw:
        port = 8080
    else:
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise SettingsError(
                f"PETASOS_PORT must be an integer from 1 to 65535; got {port_raw!r}."
            ) from exc
        if not (1 <= port <= 65535):
            raise SettingsError(
                f"PETASOS_PORT must be an integer from 1 to 65535; got {port_raw!r}."
            )

    return Settings(db_path=db_path, archive_dir=archive_dir, host=host, port=port)


def build_app(settings: Settings, *, clock: Callable[[], datetime]) -> Any:
    """Creates the database file's folder and `settings.archive_dir` if missing,
    migrates, and returns
    `RequestSizeLimit(MaintenanceRunner(create_app(db, clock=clock), ...))`.
    Exactly one worker and one SQLite file: the MCP session bindings live in
    process memory (003 spec 3.3)."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    db.migrate()
    settings.archive_dir.mkdir(parents=True, exist_ok=True)

    inner = create_app(db, clock=clock)
    runner = maintenance.MaintenanceRunner(
        inner,
        db=db,
        clock=clock,
        archive_dir=settings.archive_dir,
        interval_s=maintenance.MAINTENANCE_INTERVAL_S,
    )
    return RequestSizeLimit(runner)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["--check"]):
        print("usage: python -m petasos.serve [--check]", file=sys.stderr)
        return 2

    try:
        settings = settings_from_environ(os.environ)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args == ["--check"]:
        build_app(settings, clock=lambda: datetime.now(UTC))
        print(f"check ok {settings.db_path}")
        return 0

    app = build_app(settings, clock=lambda: datetime.now(UTC))
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        timeout_graceful_shutdown=30,
        timeout_keep_alive=65,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["Settings", "SettingsError", "build_app", "main", "settings_from_environ"]
