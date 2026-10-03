"""The maintenance tick: expire visitor sessions, rotate the ledger when it is
long, prune old archives; and `reset_demo`, the operator command that expires
everything at once (spec 005 5.5 to 5.8). Constants are read as
`maintenance.NAME` at call time (never imported with `from`), so a test can
monkeypatch them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from petasos.ledger import chain
from petasos.ledger.store import LEDGER_SCHEMA
from petasos.sessions.store import SessionStore
from petasos.storage import Database
from petasos.trust.record import canonical_json

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_S = 60
LEDGER_ROTATE_ROWS = 10_000
ROTATION_BATCH_ROWS = 1_000
ARCHIVE_KEEP_DAYS = 90
ARCHIVE_BUDGET_BYTES = 200_000_000
ROTATION_BACKOFF_TICKS = 10

_ARCHIVE_NAME_RE = re.compile(r"^ledger-(\d{8}T\d{6}Z)\.sqlite$")


@dataclasses.dataclass(frozen=True)
class RotationReport:
    archive: Path
    rows: int
    first_id: int
    last_id: int
    last_hash: str


@dataclasses.dataclass(frozen=True)
class MaintenanceReport:
    sessions_expired: int
    rotation: RotationReport | None
    archives_pruned: int
    errors: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ResetReport:
    sessions_marked: int
    sessions_expired: int


def _parse_archive_timestamp(name: str) -> datetime | None:
    match = _ARCHIVE_NAME_RE.match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def prune_archives(archive_dir: Path, *, now: datetime) -> int:
    """Deletes archives older than `ARCHIVE_KEEP_DAYS` days, then, oldest first,
    enough more that the rest total at most `ARCHIVE_BUDGET_BYTES`. The file name's
    timestamp decides, never the file's modification time; a name that matches the
    pattern but does not parse is skipped, and nothing else in the folder is
    touched."""
    candidates: list[tuple[datetime, Path, int]] = []
    for entry in archive_dir.iterdir():
        if not entry.is_file():
            continue
        ts = _parse_archive_timestamp(entry.name)
        if ts is None:
            continue
        candidates.append((ts, entry, entry.stat().st_size))

    pruned = 0
    cutoff = now - timedelta(days=ARCHIVE_KEEP_DAYS)
    remaining: list[tuple[datetime, Path, int]] = []
    for ts, path, size in candidates:
        if ts < cutoff:
            path.unlink()
            pruned += 1
        else:
            remaining.append((ts, path, size))

    remaining.sort(key=lambda item: item[0])
    total = sum(size for _, _, size in remaining)
    for ts, path, size in remaining:
        if total <= ARCHIVE_BUDGET_BYTES:
            break
        path.unlink()
        pruned += 1
        total -= size

    return pruned


def _row_digest_update(digest, row: sqlite3.Row) -> None:
    digest.update(canonical_json(dict(row)))


def _copy_rows(
    conn: sqlite3.Connection, archive_conn: sqlite3.Connection, digest
) -> tuple[int, int | None, int | None, str | None]:
    rows_count = 0
    first_id: int | None = None
    last_id: int | None = None
    last_hash: str | None = None
    cursor = conn.execute("SELECT * FROM ledger ORDER BY id")
    while True:
        batch = cursor.fetchmany(ROTATION_BATCH_ROWS)
        if not batch:
            break
        for row in batch:
            rows_count += 1
            if first_id is None:
                first_id = row["id"]
            last_id = row["id"]
            last_hash = row["hash"]
            archive_conn.execute(
                "INSERT INTO ledger "
                "(id, ts, kind, scope, actor, verb, tier, grant_id, record_hash, "
                " detail_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"],
                    row["ts"],
                    row["kind"],
                    row["scope"],
                    row["actor"],
                    row["verb"],
                    row["tier"],
                    row["grant_id"],
                    row["record_hash"],
                    row["detail_json"],
                    row["prev_hash"],
                    row["hash"],
                ),
            )
            _row_digest_update(digest, row)
    return rows_count, first_id, last_id, last_hash


def _do_rotate(conn: sqlite3.Connection, archive_path: Path, now: datetime) -> RotationReport:
    archive_conn = sqlite3.connect(archive_path, isolation_level=None)
    archive_conn.row_factory = sqlite3.Row
    try:
        archive_conn.executescript(LEDGER_SCHEMA)
        source_digest = sha256()
        archive_conn.execute("BEGIN")
        rows_count, first_id, last_id, last_hash = _copy_rows(conn, archive_conn, source_digest)
        archive_conn.execute("COMMIT")

        read_digest = sha256()
        archive_rows = archive_conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
        for row in archive_rows:
            _row_digest_update(read_digest, row)
        if read_digest.hexdigest() != source_digest.hexdigest():
            raise RuntimeError("rotation: archive content does not match the source")

        if chain.verify(archive_conn) is not None:
            raise RuntimeError("rotation: the archive's own chain does not verify")
        if chain.verify(conn) is not None:
            raise RuntimeError("rotation: the source chain does not verify")

        archive_count = archive_conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
        if archive_count != rows_count:
            raise RuntimeError("rotation: archive row count does not match")
        if not archive_rows or archive_rows[-1]["hash"] != last_hash:
            raise RuntimeError("rotation: archive's last hash does not match")
    finally:
        archive_conn.close()

    assert first_id is not None and last_id is not None and last_hash is not None
    conn.execute("DELETE FROM ledger")
    chain.append(
        conn,
        now=now,
        kind="maintenance",
        scope="global",
        actor="system",
        verb=None,
        tier=None,
        grant_id=None,
        record_hash=None,
        detail={
            "code": "rotated",
            "archived_rows": rows_count,
            "archived_first_id": first_id,
            "archived_last_id": last_id,
            "archived_last_hash": last_hash,
            "archive": archive_path.name,
        },
    )
    return RotationReport(
        archive=archive_path,
        rows=rows_count,
        first_id=first_id,
        last_id=last_id,
        last_hash=last_hash,
    )


def rotate_ledger(db: Database, *, archive_dir: Path, now: datetime) -> RotationReport | None:
    """Everything happens inside one `db.transaction()`, taken first, so the
    count, the copy, the checks, and the delete all see one frozen table and no
    row can be appended meanwhile. Below `LEDGER_ROTATE_ROWS` rows, returns `None`
    and writes nothing. Any failure raises, the transaction rolls back with every
    row in place, and this attempt's archive file (only that file) is removed."""
    with db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
        if count < LEDGER_ROTATE_ROWS:
            return None

        archive_path = archive_dir / f"ledger-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}.sqlite"
        archive_path.touch(exist_ok=False)

        try:
            return _do_rotate(conn, archive_path, now)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise


def reset_demo(db: Database, *, now: datetime) -> ResetReport:
    """Step 1 (one transaction): expire every live session and identity at once
    (both tables, since authentication reads `identities.expires_at`) and record
    one `maintenance` ledger row. Step 2 (the store's own transaction):
    `SessionStore.expire`, the normal delete order. If step 2 raises, the
    exception propagates after step 1 has committed, leaving the state an
    ordinary expiry leaves before cleanup."""
    now_s = now.isoformat()
    with db.transaction() as conn:
        sessions_cur = conn.execute(
            "UPDATE sessions SET expires_at=? WHERE expires_at>?", (now_s, now_s)
        )
        conn.execute("UPDATE identities SET expires_at=? WHERE expires_at>?", (now_s, now_s))
        sessions_marked = sessions_cur.rowcount
        chain.append(
            conn,
            now=now,
            kind="maintenance",
            scope="global",
            actor="system",
            verb=None,
            tier=None,
            grant_id=None,
            record_hash=None,
            detail={"code": "reset", "sessions": sessions_marked},
        )

    sessions_expired = SessionStore(db, clock=lambda: now).expire(now)
    return ResetReport(sessions_marked=sessions_marked, sessions_expired=sessions_expired)


def run_maintenance(
    db: Database, *, now: datetime, archive_dir: Path, skip_rotation: bool = False
) -> MaintenanceReport:
    """Runs `expire`, `rotate` (unless `skip_rotation`), and `prune`, each in its
    own `try` so one step's failure never stops the others or raises out of this
    function. Never deletes or lowers `abuse_counters`, `canaries`, or a `global`
    scoped `quota_events` row (spec 003 3.22)."""
    errors: list[str] = []

    sessions_expired = 0
    try:
        sessions_expired = SessionStore(db, clock=lambda: now).expire(now)
    except Exception:  # noqa: BLE001 - one step's failure never stops the tick
        errors.append("expire")
        logger.warning("maintenance step failed: %s", "expire")

    rotation: RotationReport | None = None
    if not skip_rotation:
        try:
            rotation = rotate_ledger(db, archive_dir=archive_dir, now=now)
        except Exception:  # noqa: BLE001
            errors.append("rotate")
            logger.warning("maintenance step failed: %s", "rotate")

    archives_pruned = 0
    try:
        archives_pruned = prune_archives(archive_dir, now=now)
    except Exception:  # noqa: BLE001
        errors.append("prune")
        logger.warning("maintenance step failed: %s", "prune")

    logger.info(
        "maintenance tick: sessions_expired=%s rotated=%s archives_pruned=%s",
        sessions_expired,
        rotation.archive.name if rotation is not None else None,
        archives_pruned,
    )
    return MaintenanceReport(
        sessions_expired=sessions_expired,
        rotation=rotation,
        archives_pruned=archives_pruned,
        errors=tuple(errors),
    )


class MaintenanceRunner:
    """Raw ASGI wrapper, attribute `app`. Passes every non-lifespan scope to the
    inner app untouched. On the `lifespan` scope, it drives the inner app's own
    lifespan handling and, once the inner app reports `startup.complete`, starts
    one background loop that ticks `run_maintenance` and sleeps `interval_s`
    between ticks (`sleep` is injectable so a test releases ticks one at a time).
    On `shutdown`, it stops the loop and waits for any tick already running in a
    worker thread to finish (cancelling the thread does not stop it; each step is
    one transaction, so a finishing tick is safe) before forwarding `shutdown` to
    the inner app (spec 005 5.5)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        db: Database,
        clock: Callable[[], datetime],
        archive_dir: Path,
        interval_s: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.app = app
        self._db = db
        self._clock = clock
        self._archive_dir = archive_dir
        self._interval_s = interval_s
        self._sleep = sleep

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        await self._run_lifespan(scope, receive, send)

    async def _run_lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        inner_receive_queue: asyncio.Queue[Message] = asyncio.Queue()
        outer_send_queue: asyncio.Queue[Message] = asyncio.Queue()

        async def inner_receive() -> Message:
            return await inner_receive_queue.get()

        async def inner_send(message: Message) -> None:
            await outer_send_queue.put(message)

        inner_task = asyncio.ensure_future(self.app(scope, inner_receive, inner_send))

        loop_task: asyncio.Task[None] | None = None
        current_tick: asyncio.Future | None = None
        skip_counter = 0

        async def tick_once() -> None:
            nonlocal current_tick, skip_counter
            now = self._clock()
            skip = skip_counter > 0
            tick_future = asyncio.ensure_future(
                asyncio.to_thread(
                    run_maintenance,
                    self._db,
                    now=now,
                    archive_dir=self._archive_dir,
                    skip_rotation=skip,
                )
            )
            current_tick = tick_future
            report = await asyncio.shield(tick_future)
            if skip:
                skip_counter -= 1
            elif "rotate" in report.errors:
                skip_counter = ROTATION_BACKOFF_TICKS

        async def loop() -> None:
            while True:
                await tick_once()
                await self._sleep(self._interval_s)

        startup_message = await receive()
        await inner_receive_queue.put(startup_message)
        startup_reply = await outer_send_queue.get()
        await send(startup_reply)
        if startup_reply["type"] == "lifespan.startup.complete":
            loop_task = asyncio.ensure_future(loop())

        shutdown_message = await receive()
        if loop_task is not None:
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
            if current_tick is not None and not current_tick.done():
                await current_tick

        await inner_receive_queue.put(shutdown_message)
        shutdown_reply = await outer_send_queue.get()
        await send(shutdown_reply)
        await inner_task


def _usage() -> int:
    print("usage: python -m petasos.sessions.maintenance reset|expire|rotate", file=sys.stderr)
    return 2


def _main(argv: list[str] | None = None) -> int:
    from petasos.serve import SettingsError, settings_from_environ

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ("reset", "expire", "rotate"):
        return _usage()
    command = args[0]

    try:
        settings = settings_from_environ(os.environ)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    db = Database(settings.db_path)
    now = datetime.now(UTC)
    try:
        if command == "reset":
            report = reset_demo(db, now=now)
            print(
                f"reset: sessions_marked={report.sessions_marked} "
                f"sessions_expired={report.sessions_expired}"
            )
        elif command == "expire":
            count = SessionStore(db, clock=lambda: now).expire(now)
            print(f"expire: sessions_expired={count}")
        else:
            rotation = rotate_ledger(db, archive_dir=settings.archive_dir, now=now)
            archive_name = rotation.archive.name if rotation is not None else "none"
            rows = rotation.rows if rotation is not None else 0
            print(f"rotate: archive={archive_name} rows={rows}")
    except Exception as exc:  # noqa: BLE001 - the class name only, never its text
        print(exc.__class__.__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())


__all__ = [
    "ARCHIVE_BUDGET_BYTES",
    "ARCHIVE_KEEP_DAYS",
    "LEDGER_ROTATE_ROWS",
    "MAINTENANCE_INTERVAL_S",
    "ROTATION_BACKOFF_TICKS",
    "ROTATION_BATCH_ROWS",
    "MaintenanceReport",
    "MaintenanceRunner",
    "ResetReport",
    "RotationReport",
    "prune_archives",
    "reset_demo",
    "rotate_ledger",
    "run_maintenance",
]
