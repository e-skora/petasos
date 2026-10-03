"""Reader test (spec 005 acceptance test 16): the whole deployed shape, end to
end, against one fixture: a tick runs at startup, a session mints, expires within
the hour one gate release later, an oversized `POST /session` is refused, the
smoke journey passes against the same client, and the ledger verifies at the end.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from support_005 import TickGate, lifespan_harness, receipt_provided

from petasos.app import create_app
from petasos.ledger import Ledger
from petasos.limits import MAX_REQUEST_BYTES, RequestSizeLimit
from petasos.sessions.maintenance import MAINTENANCE_INTERVAL_S, MaintenanceRunner
from petasos.storage import Database

if TYPE_CHECKING:
    from conftest import FrozenClock

BASE_URL = "http://testserver"

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_journey.py"
_SPEC = importlib.util.spec_from_file_location("smoke_journey", _MODULE_PATH)
smoke_journey = importlib.util.module_from_spec(_SPEC)
sys.modules["smoke_journey"] = smoke_journey
_SPEC.loader.exec_module(smoke_journey)


async def test_the_deployed_shape_end_to_end(tmp_path: Path, frozen_clock: FrozenClock) -> None:
    db_path = tmp_path / "p.sqlite"
    archive_dir = tmp_path / "archive"
    db = Database(db_path)
    db.migrate()
    archive_dir.mkdir(parents=True, exist_ok=True)

    gate = TickGate()
    inner = create_app(db, clock=frozen_clock)
    runner = MaintenanceRunner(
        inner,
        db=db,
        clock=frozen_clock,
        archive_dir=archive_dir,
        interval_s=MAINTENANCE_INTERVAL_S,
        sleep=gate,
    )
    built = RequestSizeLimit(runner)
    app = receipt_provided(built, db)

    async with lifespan_harness(app):
        await gate.wait_until_sleeping()  # the first tick ran at startup

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE_URL
        ) as client:
            minted = (await client.post("/session")).json()
            session_id = minted["session"]

            client.headers["Authorization"] = f"Bearer {minted['tokens']['agent']}"
            async with (
                streamable_http_client(f"{BASE_URL}/mcp", http_client=client) as (
                    read_stream,
                    write_stream,
                ),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                await session.call_tool("get_ticket", {"ticket": 1})

            frozen_clock.advance(61 * 60)
            await gate.release()
            await gate.wait_until_sleeping()

            conn = db.connect()
            try:
                sessions_left = conn.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE id=?", (session_id,)
                ).fetchone()["n"]
                ledger_rows = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
            finally:
                conn.close()
            assert sessions_left == 0
            assert ledger_rows > 0

            oversized = await client.post("/session", content=b"x" * (MAX_REQUEST_BYTES + 1))
            assert oversized.status_code == 413

            report = await smoke_journey.run_journey(client, base_url=BASE_URL)
            assert report.exit_code == 0, report.outcome

        assert Ledger(db).verify() is None
