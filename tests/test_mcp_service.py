"""`mcp/service.py`: the call logic both front doors share (spec 4.8). Service
halves of acceptance tests 10 (shape parity) and 16 (the moved names are the same
objects, and `petasos.mcp` never imports `petasos.owner`)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from petasos.helpdesk.tools import ReadCapture
from petasos.mcp import server as mcp_server_module
from petasos.mcp import tools as mcp_tools_module
from petasos.mcp.identity import Identity
from petasos.mcp.service import (
    ALLOWED_TOOLS,
    PROPOSABLE_TOOLS,
    TOOL_ARG_SPEC,
    approval_card,
    approve_and_run,
    shape_ok,
)
from petasos.mcp.tools import GateFactory
from petasos.memory.store import MemoryTier
from petasos.sessions.store import SessionStore
from petasos.storage import Database


def test_service_objects_are_shared_with_tools_and_server() -> None:
    assert mcp_tools_module.TOOL_ARG_SPEC is TOOL_ARG_SPEC
    assert mcp_tools_module.ALLOWED_TOOLS is ALLOWED_TOOLS
    assert mcp_server_module.TOOL_ARG_SPEC is TOOL_ARG_SPEC
    assert mcp_server_module.ALLOWED_TOOLS is ALLOWED_TOOLS
    assert mcp_server_module._shape_ok is shape_ok


def test_proposable_tools_is_exactly_the_four_write_tools() -> None:
    assert PROPOSABLE_TOOLS == {
        "add_internal_note",
        "reply_to_customer",
        "issue_refund",
        "delete_ticket",
    }


def test_shape_ok_matches_the_003_shape_list() -> None:
    assert shape_ok("get_ticket", {"ticket": 3})
    assert not shape_ok("get_ticket", {"ticket": True})
    assert not shape_ok("get_ticket", {"ticket": "3"})
    assert not shape_ok("get_ticket", {"ticket": 3.0})
    assert not shape_ok("get_ticket", {"ticket": 3, "extra": 1})
    assert not shape_ok("get_ticket", {})
    assert not shape_ok("issue_refund", {"ticket": 1, "amount": 42, "currency": "USD"})
    assert not shape_ok("not-a-real-tool", {})


def test_approval_card_has_exactly_the_six_mcp_keys(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    store = SessionStore(db, clock=frozen_clock)
    minted = store.mint()
    identity = Identity(
        id=f"{minted.session}-agent",
        role="agent",
        scope=minted.session,
        ceiling=MemoryTier.T3,
        may_stage=True,
        approver=False,
        expires_at=minted.expires_at,
    )
    factory = GateFactory(db, frozen_clock)
    gate = factory.for_call(identity, ReadCapture())
    staged = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}, proposer=identity.id
    )
    assert staged.code == "staged"

    grant = gate.grants.list_pending(viewer=f"{minted.session}-owner")[0]
    card = approval_card(grant)
    assert set(card) == {"grant_id", "token", "verb", "tier", "record", "expires_at"}
    assert card["verb"] == "ISSUE-REFUND"
    assert card["tier"] == "L5"
    assert card["record"]["amount_minor"] == 4200


def test_approve_and_run_executes_on_approval(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    store = SessionStore(db, clock=frozen_clock)
    minted = store.mint()
    agent = Identity(
        id=f"{minted.session}-agent",
        role="agent",
        scope=minted.session,
        ceiling=MemoryTier.T3,
        may_stage=True,
        approver=False,
        expires_at=minted.expires_at,
    )
    owner = Identity(
        id=f"{minted.session}-owner",
        role="owner",
        scope=minted.session,
        ceiling=MemoryTier.T4,
        may_stage=True,
        approver=True,
        expires_at=minted.expires_at,
    )
    factory = GateFactory(db, frozen_clock)
    agent_gate = factory.for_call(agent, ReadCapture())
    staged = agent_gate.propose(
        "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}, proposer=agent.id
    )
    owner_gate = factory.for_call(owner, ReadCapture())
    card = approval_card(owner_gate.grants.list_pending(viewer=owner.id)[0])

    result = approve_and_run(
        owner_gate, token=card["token"], verb="ISSUE-REFUND", approver=owner.id
    )
    assert result.code == "executed"
    assert result.grant_id == staged.grant_id


def test_mcp_never_imports_owner_in_a_fresh_subprocess(tmp_path: Path) -> None:
    script = (
        "import sys\n"
        "import petasos.mcp.service\n"
        "import petasos.mcp.tools\n"
        "assert not any(name.startswith('petasos.owner') for name in sys.modules), sys.modules.keys()\n"
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
