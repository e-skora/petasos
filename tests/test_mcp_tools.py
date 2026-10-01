"""`GateFactory`, `result_json`/`tool_result`, and the tool-visibility and
argument-shape tables the `ToolAccessMiddleware` reads (spec 3.4, 3.6, 3.8, 3.15)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from petasos.helpdesk.tools import ReadCapture
from petasos.mcp.identity import Identity
from petasos.mcp.outcomes import MCP_SENTENCES
from petasos.mcp.server import ALLOWED_TOOLS, TOOL_ARG_SPEC, _shape_ok
from petasos.mcp.tools import GateFactory, mcp_refusal, result_json, tool_result
from petasos.memory.store import MemoryTier
from petasos.sessions.store import SessionStore
from petasos.storage import Database
from petasos.trust import SENTENCES, Tier, make_result


def test_gate_factory_scopes_the_gate_and_its_approver(tmp_sqlite: Path, frozen_clock) -> None:
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
    result = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "10.00", "currency": "USD"}, proposer=identity.id
    )
    assert result.code == "staged"

    grants = gate.grants.list_pending(viewer=f"{minted.session}-owner")
    assert len(grants) == 1


def test_result_json_has_no_token_and_carries_data_only_when_given() -> None:
    staged = make_result("staged", tier=Tier.L5, verb="ISSUE-REFUND", grant_id=3, scope="s1")
    payload = result_json(staged)
    assert "data" not in payload
    assert payload["tier"] == "L5"
    assert payload["grant_id"] == 3
    assert "token" not in json.dumps(payload)

    with_data = result_json(staged, data={"number": 3})
    assert with_data["data"] == {"number": 3}


def test_tool_result_is_error_for_refused_and_failed_codes() -> None:
    ok = tool_result(make_result("executed", tier=Tier.L1, verb="READ"))
    assert ok.is_error is False

    refused = tool_result(make_result("refused/invalid_arguments"))
    assert refused.is_error is True

    failed = tool_result(make_result("failed/execution_error"))
    assert failed.is_error is True


def test_mcp_refusal_has_no_underscore_free_sentence_and_is_error() -> None:
    refusal = mcp_refusal("refused/not_allowed")
    assert refusal.is_error is True
    payload = json.loads(refusal.content[0].text)
    assert payload["status"] == "refused/not_allowed"
    assert "_" not in payload["explanation"]


def test_tool_sets_match_the_three_roles() -> None:
    assert ALLOWED_TOOLS["visitor"] == {"ping", "list_tickets", "get_ticket", "recall"}
    assert ALLOWED_TOOLS["agent"] == ALLOWED_TOOLS["visitor"] | {
        "add_internal_note",
        "reply_to_customer",
        "issue_refund",
        "delete_ticket",
    }
    assert ALLOWED_TOOLS["owner"] == ALLOWED_TOOLS["agent"] | {
        "list_pending_approvals",
        "approve",
        "abort",
    }


def test_shape_ok_rejects_wrong_types_and_extra_or_missing_keys() -> None:
    assert _shape_ok("get_ticket", {"ticket": 3})
    assert not _shape_ok("get_ticket", {"ticket": True})
    assert not _shape_ok("get_ticket", {"ticket": "3"})
    assert not _shape_ok("get_ticket", {"ticket": 3.0})
    assert not _shape_ok("get_ticket", {"ticket": 3, "extra": 1})
    assert not _shape_ok("get_ticket", {})
    assert not _shape_ok("issue_refund", {"ticket": 1, "amount": 42, "currency": "USD"})
    assert not _shape_ok("not-a-real-tool", {})


def test_create_app_signature_has_no_tokens_parameter() -> None:
    from petasos.app import create_app

    params = list(inspect.signature(create_app).parameters)
    assert "tokens" not in params
    assert params == ["db", "clock"]


def test_every_mcp_sentence_has_no_code_id_or_underscore() -> None:
    for code, sentence in {**SENTENCES, **MCP_SENTENCES}.items():
        assert "_" not in sentence, code
        assert code not in sentence, code


def test_no_tool_arg_spec_parameter_contains_forbidden_words() -> None:
    forbidden = ("risk", "tier", "flag", "approver", "scope")
    for name, spec in TOOL_ARG_SPEC.items():
        for param in spec:
            assert not any(word in param for word in forbidden), (name, param)
        if "verb" in spec:
            assert name == "approve"
