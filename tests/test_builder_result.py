"""Tests for the builder result checker (`.github/scripts/builder_result.py`).

Everything that decides is pure: `derive_expected(start, now)` turns the trusted starting facts
into what the builder was supposed to do, and `check(before, after, result, build_job)` judges
the result. Almost every test builds a state by hand and calls one of them. `main()` is
exercised against a fake `gh` runner and explicit environment dicts: no network, no
environment variables read, no real home folder. Files go only under pytest's `tmp_path`.

The workflow itself is checked as text (no YAML parser is a project dependency) for the
boundary the phase 1 third-delta review D1 and D5 asked for.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github/scripts/builder_result.py"
WORKFLOW_PATH = ROOT / ".github/workflows/claude-builder.yml"
_SPEC = importlib.util.spec_from_file_location("builder_result", MODULE_PATH)
builder_result = importlib.util.module_from_spec(_SPEC)
sys.modules["builder_result"] = builder_result
_SPEC.loader.exec_module(builder_result)

check = builder_result.check
parse_result = builder_result.parse_result
build_snapshot = builder_result.build_snapshot
derive_expected = builder_result.derive_expected

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
CHANGE = "005-deploy-fly-cloudflare"
BRANCH = f"build/{CHANGE}"
OTHER_CHANGE = "006-other"
OTHER_BRANCH = f"build/{OTHER_CHANGE}"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
OLD = (NOW - timedelta(hours=10)).isoformat()
FRESH = (NOW - timedelta(hours=1)).isoformat()
BEHIND = "the branch is behind `main`; merge `main` into it"
WAIT_CODEX = "no Codex review for the head commit"


def pull(number, change=CHANGE, head=SHA_A, draft=False, labels=(), **extra):
    row = {
        "number": number,
        "title": f"[build] {change}",
        "branch": f"build/{change}",
        "head": head,
        "draft": draft,
        "labels": list(labels),
    }
    row.update(extra)
    return row


def exp(eligible=None, repair_due=(), exhaust_due=(), stale=(), unverifiable=()):
    return {
        "eligible": eligible,
        "repair_due": list(repair_due),
        "exhaust_due": list(exhaust_due),
        "stale_branches": list(stale),
        "unverifiable": list(unverifiable),
    }


def state(pulls=(), branches=None, expected=None):
    """A saved state. By default every pull request's branch exists at its head, and the
    starting evidence says nothing is eligible and nothing needs a repair."""
    pulls = list(pulls)
    if branches is None:
        branches = {p["branch"]: p["head"] for p in pulls}
    return {"pulls": pulls, "branches": dict(branches), "expected": expected or exp()}


def after_state(pulls=(), branches=None, closed=None, reported=None):
    out = state(pulls, branches)
    out.pop("expected")
    if closed is not None:
        out["closed"] = closed
    if reported is not None:
        out["reported"] = reported
    return out


def result(action, pull_request=None, change=None, head=None, reason="ok"):
    return json.dumps(
        {
            "action": action,
            "pull_request": pull_request,
            "change": change,
            "head": head,
            "reason": reason,
        }
    )


def noop(reason):
    return result("no-op", reason=reason)


EMPTY = state()
EMPTY_AFTER = after_state()
START_ELIGIBLE = state(expected=exp(eligible=CHANGE))


# --- Rule 1: missing, empty, unparseable, or schema-invalid result ---------------------------


@pytest.mark.parametrize("raw", [None, "", "   \n"])
def test_missing_or_empty_result_is_a_problem(raw):
    problems = check(EMPTY, EMPTY_AFTER, raw)
    assert problems == ["The builder returned no structured result."]


def test_unparseable_result_is_a_problem():
    problems = check(EMPTY, EMPTY_AFTER, "{not json")
    assert problems == ["The builder's structured result is not valid JSON."]


def test_result_that_is_not_an_object_is_a_problem():
    assert check(EMPTY, EMPTY_AFTER, "[1, 2]") == [
        "The builder's structured result is not a JSON object."
    ]


def test_result_with_a_missing_key_is_a_problem():
    raw = json.dumps({"action": "no-op", "pull_request": None, "change": None, "head": None})
    problems = check(EMPTY, EMPTY_AFTER, raw)
    assert len(problems) == 1
    assert "missing reason" in problems[0]


def test_result_with_an_extra_key_is_a_problem():
    body = json.loads(noop("nothing_eligible"))
    body["extra"] = 1
    problems = check(EMPTY, EMPTY_AFTER, json.dumps(body))
    assert len(problems) == 1
    assert "unexpected extra" in problems[0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action", "finished"),
        ("pull_request", "7"),
        ("pull_request", True),
        ("pull_request", 0),
        ("head", "A" * 40),
        ("head", "abc123"),
        ("reason", None),
        ("change", 5),
    ],
)
def test_result_with_a_wrong_type_or_value_is_a_problem(field, value):
    body = json.loads(result("started", 7, CHANGE, SHA_A))
    body[field] = value
    problems = check(EMPTY, EMPTY_AFTER, json.dumps(body))
    assert len(problems) >= 1
    assert "invalid" in problems[0]


def test_a_valid_result_object_is_accepted_without_json_text():
    parsed, why = parse_result(json.loads(noop("nothing_eligible")))
    assert why is None
    assert parsed["action"] == "no-op"


# --- Rule 7: null exactly for no-op ----------------------------------------------------------


def test_noop_with_a_pull_request_number_is_a_problem():
    raw = result("no-op", pull_request=7, reason="nothing_eligible")
    problems = check(EMPTY, EMPTY_AFTER, raw)
    assert len(problems) == 1
    assert "no-op must have" in problems[0]


@pytest.mark.parametrize(
    "raw",
    [
        result("started", None, CHANGE, SHA_A),
        result("repaired", 7, None, SHA_A),
        result("synced", 7, CHANGE, None),
        result("blocked", None, None, None),
    ],
)
def test_non_noop_with_a_null_field_is_a_problem(raw):
    problems = check(EMPTY, EMPTY_AFTER, raw)
    assert len(problems) == 1
    assert "must not be null" in problems[0]


def test_noop_with_all_nulls_is_valid():
    assert check(EMPTY, EMPTY_AFTER, noop("nothing_eligible")) == []


# --- Rule 2: a claimed branch needs a pull request (the 2026-10-02 failure) ------------------


def test_2026_10_02_started_with_a_made_up_pull_request_number_fails():
    after = after_state(branches={BRANCH: SHA_A})  # only the empty claim commit
    problems = check(START_ELIGIBLE, after, result("started", 99, CHANGE, SHA_A))
    assert f"The builder claimed {BRANCH} but opened no pull request." in problems
    assert any("no open or merged pull request has that number" in p for p in problems)


def test_2026_10_02_missing_result_with_an_orphan_claim_branch_fails_both_ways():
    after = after_state(branches={BRANCH: SHA_A})
    problems = check(EMPTY, after, None)
    assert problems[0] == "The builder returned no structured result."
    assert f"The builder claimed {BRANCH} but opened no pull request." in problems
    assert len(problems) == 2


def test_orphan_claim_branch_fails_even_when_the_result_says_noop():
    after = after_state(branches={BRANCH: SHA_A})
    problems = check(EMPTY, after, noop("nothing_eligible"))
    assert f"The builder claimed {BRANCH} but opened no pull request." in problems


def test_claim_branch_with_its_pull_request_passes_rule_2():
    after = after_state([pull(7)])
    assert check(START_ELIGIBLE, after, result("started", 7, CHANGE, SHA_A)) == []


def test_branch_that_already_existed_before_without_a_pull_request_is_not_a_new_claim():
    before = state(branches={BRANCH: SHA_A})
    after = after_state(branches={BRANCH: SHA_A})
    assert check(before, after, noop("nothing_eligible")) == []


# --- Rule 3: started -------------------------------------------------------------------------


def test_started_passes_when_the_new_pull_request_matches():
    after = after_state([pull(7)])
    assert check(START_ELIGIBLE, after, result("started", 7, CHANGE, SHA_A)) == []


def test_started_fails_when_the_pull_request_does_not_exist():
    problems = check(START_ELIGIBLE, EMPTY_AFTER, result("started", 7, CHANGE, SHA_A))
    assert problems == [
        (
            "The builder said it started pull request 7, "
            "but no open or merged pull request has that number."
        )
    ]


def test_started_fails_when_the_pull_request_already_existed():
    before = state([pull(7)], expected=exp(eligible=CHANGE))
    problems = check(before, after_state([pull(7)]), result("started", 7, CHANGE, SHA_A))
    assert any("already existed before the run" in p for p in problems)


def test_started_fails_when_the_title_is_wrong():
    wrong = pull(7)
    wrong["title"] = "[build] 006-other"
    problems = check(START_ELIGIBLE, after_state([wrong]), result("started", 7, CHANGE, SHA_A))
    assert any('is not titled "[build] 005-deploy-fly-cloudflare"' in p for p in problems)


def test_started_fails_when_the_head_is_not_the_reported_head():
    after = after_state([pull(7, head=SHA_B)])
    problems = check(START_ELIGIBLE, after, result("started", 7, CHANGE, SHA_A))
    assert any("not the" in p and SHA_A in p for p in problems)


def test_started_fails_when_another_change_was_the_eligible_one():
    before = state(expected=exp(eligible=OTHER_CHANGE))
    problems = check(before, after_state([pull(7)]), result("started", 7, CHANGE, SHA_A))
    assert any("the eligible change at the start was 006-other" in p for p in problems)


def test_started_fails_when_nothing_was_eligible():
    problems = check(EMPTY, after_state([pull(7)]), result("started", 7, CHANGE, SHA_A))
    assert any("the eligible change at the start was none" in p for p in problems)


def test_started_fails_when_a_repair_was_due_first():
    before = state([pull(8, OTHER_CHANGE, head=SHA_B)], expected=exp(CHANGE, repair_due=[8]))
    after = after_state([pull(8, OTHER_CHANGE, head=SHA_B), pull(7)])
    problems = check(before, after, result("started", 7, CHANGE, SHA_A))
    assert any("pull request 8 needed a repair or a draft first" in p for p in problems)


# --- Rule 4: repaired and synced -------------------------------------------------------------


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_passes_when_the_head_changed_to_the_reported_head(action):
    before = state([pull(7, head=SHA_A)], expected=exp(repair_due=[7]))
    after = after_state([pull(7, head=SHA_B)])
    assert check(before, after, result(action, 7, CHANGE, SHA_B)) == []


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_head_did_not_change(action):
    before = state([pull(7, head=SHA_A)], expected=exp(repair_due=[7]))
    after = after_state([pull(7, head=SHA_A)])
    problems = check(before, after, result(action, 7, CHANGE, SHA_A))
    assert any("head commit did not change" in p for p in problems)


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_head_is_not_the_reported_head(action):
    before = state([pull(7, head=SHA_A)], expected=exp(repair_due=[7]))
    after = after_state([pull(7, head=SHA_B)])
    problems = check(before, after, result(action, 7, CHANGE, SHA_C))
    assert len(problems) == 1
    assert SHA_C in problems[0]


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_pull_request_did_not_exist_before(action):
    after = after_state([pull(7, head=SHA_B)])
    problems = check(EMPTY, after, result(action, 7, CHANGE, SHA_B))
    assert any("was not open before the run" in p for p in problems)


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_pull_request_is_gone_after(action):
    before = state([pull(7, head=SHA_A)], expected=exp(repair_due=[7]))
    problems = check(before, EMPTY_AFTER, result(action, 7, CHANGE, SHA_B))
    assert any("no open or merged pull request has that number" in p for p in problems)


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_no_repair_was_due(action):
    before = state([pull(7, head=SHA_A)])
    after = after_state([pull(7, head=SHA_B)])
    problems = check(before, after, result(action, 7, CHANGE, SHA_B))
    assert problems == ["Pull request 7 did not need a repair at the start of the run."]


def test_repair_fails_when_both_repairs_were_already_used():
    before = state([pull(7, head=SHA_A)], expected=exp(exhaust_due=[7]))
    after = after_state([pull(7, head=SHA_B)])
    problems = check(before, after, result("repaired", 7, CHANGE, SHA_B))
    assert any("used both repairs" in p for p in problems)


# --- Rule 5: blocked -------------------------------------------------------------------------


def test_blocked_passes_when_a_ready_pull_request_became_a_draft():
    before = state([pull(7, draft=False)], expected=exp(exhaust_due=[7]))
    after = after_state([pull(7, draft=True)])
    assert check(before, after, result("blocked", 7, CHANGE, SHA_A, "flagged")) == []


def test_blocked_passes_when_a_new_pull_request_opened_as_a_draft():
    after = after_state([pull(7, draft=True)])
    assert check(EMPTY, after, result("blocked", 7, CHANGE, SHA_A, "flagged")) == []


def test_blocked_fails_when_the_pull_request_is_not_a_draft():
    after = after_state([pull(7, draft=False)])
    problems = check(EMPTY, after, result("blocked", 7, CHANGE, SHA_A, "flagged"))
    assert problems == ["The builder said pull request 7 is blocked, but it is not an open draft."]


def test_blocked_fails_when_the_pull_request_does_not_exist():
    problems = check(EMPTY, EMPTY_AFTER, result("blocked", 7, CHANGE, SHA_A, "flagged"))
    assert any("no open or merged pull request has that number" in p for p in problems)


# --- Rule 6: no-op ---------------------------------------------------------------------------


def test_noop_nothing_eligible_passes_with_no_build_pull_request():
    assert check(EMPTY, EMPTY_AFTER, noop("nothing_eligible")) == []


def test_noop_nothing_eligible_passes_when_all_pull_requests_are_draft_or_held():
    pulls = [pull(7, draft=True), pull(8, OTHER_CHANGE, labels=["hold"])]
    assert check(state(pulls), after_state(pulls), noop("nothing_eligible")) == []


def test_noop_nothing_eligible_fails_when_a_ready_pull_request_is_open():
    pulls = [pull(7)]
    problems = check(state(pulls), after_state(pulls), noop("nothing_eligible"))
    assert len(problems) == 1
    assert "neither a draft nor held" in problems[0]


def test_noop_waiting_on_planning_passes_with_a_draft_pull_request():
    pulls = [pull(7, draft=True)]
    assert check(state(pulls), after_state(pulls), noop("waiting_on_planning")) == []


def test_noop_waiting_on_planning_fails_without_a_draft_pull_request():
    pulls = [pull(7)]
    problems = check(state(pulls), after_state(pulls), noop("waiting_on_planning"))
    assert any("no open build pull request is a draft" in p for p in problems)


def test_noop_held_passes_with_a_held_pull_request():
    pulls = [pull(7, labels=["hold", "other"])]
    assert check(state(pulls), after_state(pulls), noop("held")) == []


def test_noop_held_fails_without_the_hold_label():
    pulls = [pull(7, labels=["other"])]
    problems = check(state(pulls), after_state(pulls), noop("held"))
    assert any("hold label" in p for p in problems)


def test_noop_waiting_on_reviews_passes_with_a_ready_unheld_pull_request():
    pulls = [pull(7)]
    assert check(state(pulls), after_state(pulls), noop("waiting_on_reviews")) == []


def test_noop_waiting_on_reviews_fails_with_only_draft_or_no_pull_requests():
    assert check(EMPTY, EMPTY_AFTER, noop("waiting_on_reviews")) != []
    drafts = [pull(7, draft=True)]
    assert check(state(drafts), after_state(drafts), noop("waiting_on_reviews")) != []


def test_noop_with_an_unknown_reason_is_a_problem():
    problems = check(EMPTY, EMPTY_AFTER, noop("tired"))
    assert problems == ['The no-op reason "tired" is not one of the allowed reasons.']


def test_noop_fails_when_an_open_pull_request_head_changed():
    before = state([pull(7, head=SHA_A)])
    after = after_state([pull(7, head=SHA_B)])
    problems = check(before, after, noop("waiting_on_reviews"))
    assert any("got a new head commit" in p for p in problems)


def test_noop_fails_when_a_pull_request_was_opened():
    after = after_state([pull(7)])
    problems = check(EMPTY, after, noop("waiting_on_reviews"))
    assert any("was opened during the run" in p for p in problems)


def test_noop_fails_when_a_branch_was_created():
    after = after_state([pull(7)], branches={BRANCH: SHA_A})
    problems = check(EMPTY, after, noop("waiting_on_reviews"))
    assert any(f"created branch {BRANCH}" in p for p in problems)


def test_noop_allows_deleting_a_stale_claim_branch():
    before = state(
        [pull(7)], branches={BRANCH: SHA_A, OTHER_BRANCH: SHA_B}, expected=exp(stale=[OTHER_BRANCH])
    )
    after = after_state([pull(7)], branches={BRANCH: SHA_A})
    assert check(before, after, noop("waiting_on_reviews")) == []


def test_noop_fails_when_a_fresh_claim_branch_was_deleted():
    before = state([pull(7)], branches={BRANCH: SHA_A, OTHER_BRANCH: SHA_B})
    after = after_state([pull(7)], branches={BRANCH: SHA_A})
    problems = check(before, after, noop("waiting_on_reviews"))
    assert any(f"deleted branch {OTHER_BRANCH}, which was not a stale claim" in p for p in problems)


def test_noop_fails_when_a_branch_with_an_open_pull_request_was_deleted():
    before = state([pull(7), pull(8, OTHER_CHANGE)])
    after = after_state([pull(7)])
    problems = check(before, after, noop("waiting_on_reviews"))
    assert any(f"deleted branch {OTHER_BRANCH}" in p for p in problems)


def test_no_op_tolerates_the_gate_merging_a_pull_request_during_the_run():
    before = state([pull(40, "006-readme-transcript-site")])
    after = after_state(closed={"40": "MERGED"})
    assert check(before, after, noop("nothing_eligible")) == []


def test_no_op_still_flags_a_deleted_branch_whose_pull_request_was_closed_not_merged():
    before = state([pull(41, "006-readme-transcript-site", head=SHA_B)])
    after = after_state(closed={"41": "CLOSED"})
    assert any("deleted branch" in p for p in check(before, after, noop("nothing_eligible")))


# --- D2: a no-op must be what the queue and the pull requests say ----------------------------


def test_d2_an_eligible_queued_change_plus_an_empty_run_fails():
    problems = check(START_ELIGIBLE, EMPTY_AFTER, noop("nothing_eligible"))
    assert problems == [f"The builder reported no-op, but {CHANGE} was eligible to build."]


@pytest.mark.parametrize("reason", builder_result.NO_OP_REASONS)
def test_d2_no_reason_excuses_an_eligible_change(reason):
    assert any(
        "was eligible to build" in p for p in check(START_ELIGIBLE, EMPTY_AFTER, noop(reason))
    )


def test_d2_a_repair_due_pull_request_cannot_masquerade_as_waiting_on_reviews():
    pulls = [pull(7)]
    before = state(pulls, expected=exp(repair_due=[7]))
    problems = check(before, after_state(pulls), noop("waiting_on_reviews"))
    assert problems == ["The builder reported no-op, but pull request 7 needed a repair."]


def test_d2_exhausted_repairs_cannot_pass_on_an_unchanged_ready_pull_request():
    pulls = [pull(7, repair_commits=2)]
    before = state(pulls, expected=exp(exhaust_due=[7]))
    problems = check(before, after_state([pull(7)]), noop("repairs_exhausted"))
    assert any("needed to be marked a draft" in p for p in problems)
    assert any("no open build pull request is a draft with 2 repair" in p for p in problems)


def test_d2_repairs_exhausted_passes_for_a_draft_that_used_both_repairs():
    before = state([pull(7, draft=True, repair_commits=2)])
    assert check(before, after_state([pull(7, draft=True)]), noop("repairs_exhausted")) == []


def test_d2_a_held_ready_pull_request_is_not_waiting_on_reviews():
    pulls = [pull(7, labels=["hold"])]
    problems = check(state(pulls), after_state(pulls), noop("waiting_on_reviews"))
    assert any("waiting_on_reviews" in p for p in problems)


def test_d2_missing_queue_evidence_fails_every_result():
    before = {"pulls": [], "branches": {}}
    problems = check(before, EMPTY_AFTER, noop("nothing_eligible"))
    assert problems == ["The starting record has no queue evidence, so nothing can be verified."]


def test_d2_unverifiable_facts_fail_instead_of_passing():
    before = state(expected=exp(unverifiable=["whether pull request 7 needed a repair"]))
    problems = check(before, EMPTY_AFTER, noop("nothing_eligible"))
    assert problems == ["Could not verify the run: whether pull request 7 needed a repair."]


# derive_expected: the starting facts, as the snapshot job records them.


def change_row(change, ratified=True, depends_on=(), grounded=True, lane_ok=True):
    return {
        "change": change,
        "ratified": ratified,
        "depends_on": list(depends_on) if depends_on is not None else None,
        "grounded": grounded,
        "lane_ok": lane_ok,
    }


def start(pulls=(), branches=None, branch_dates=None, merged=(), changes=()):
    out = state(pulls, branches)
    out.pop("expected")
    out["branch_dates"] = dict(branch_dates or {})
    out["merged_changes"] = list(merged)
    out["changes"] = list(changes)
    return out


def ready_pull(number=7, change=CHANGE, reasons=(BEHIND,), age=OLD, repairs=0, **kw):
    return pull(
        number,
        change,
        status_head=kw.pop("status_head", SHA_A),
        status_reasons=list(reasons),
        head_date=age,
        repair_commits=repairs,
        **kw,
    )


def test_derive_picks_the_lowest_numbered_eligible_change():
    facts = start(
        merged=["004-owner-api"],
        changes=[
            change_row("007-demo-app", depends_on=["004-owner-api"]),
            change_row("006-readme", depends_on=["004-owner-api", "007-demo-app"]),
        ],
    )
    assert derive_expected(facts, NOW)["eligible"] == "007-demo-app"


@pytest.mark.parametrize(
    "row",
    [
        change_row("007-demo-app", ratified=False, depends_on=["004-owner-api"]),
        change_row("007-demo-app", grounded=False, depends_on=["004-owner-api"]),
        change_row("007-demo-app", lane_ok=False, depends_on=["004-owner-api"]),
        change_row("007-demo-app", depends_on=["005-deploy-fly-cloudflare"]),
        change_row("007-demo-app", depends_on=None),
    ],
)
def test_derive_finds_nothing_when_a_rule_fails(row):
    facts = start(merged=["004-owner-api"], changes=[row])
    assert derive_expected(facts, NOW)["eligible"] is None


def test_derive_skips_a_change_already_built_or_in_flight():
    rows = [change_row("004-owner-api"), change_row("007-demo-app")]
    facts = start([pull(9, "007-demo-app")], merged=["004-owner-api"], changes=rows)
    assert derive_expected(facts, NOW)["eligible"] is None


def test_derive_treats_a_stale_claim_as_cleared_and_a_fresh_one_as_taken():
    rows = [change_row("007-demo-app")]
    branch = "build/007-demo-app"
    stale = start(branches={branch: SHA_A}, branch_dates={branch: OLD}, changes=rows)
    fresh = start(branches={branch: SHA_A}, branch_dates={branch: FRESH}, changes=rows)
    assert derive_expected(stale, NOW)["eligible"] == "007-demo-app"
    assert derive_expected(stale, NOW)["stale_branches"] == [branch]
    assert derive_expected(fresh, NOW)["eligible"] is None


def test_derive_marks_repair_due_only_for_an_old_head_with_a_repair_reason():
    assert derive_expected(start([ready_pull()]), NOW)["repair_due"] == [7]
    young = start([ready_pull(age=FRESH)])
    assert derive_expected(young, NOW)["repair_due"] == []
    waiting = start([ready_pull(reasons=[WAIT_CODEX])])
    assert derive_expected(waiting, NOW)["repair_due"] == []
    other_head = start([ready_pull(status_head=SHA_B)])
    assert derive_expected(other_head, NOW)["repair_due"] == []
    draft = start([ready_pull(draft=True)])
    assert derive_expected(draft, NOW)["repair_due"] == []


def test_derive_moves_a_pull_request_with_two_repairs_to_draft_due():
    out = derive_expected(start([ready_pull(repairs=2)]), NOW)
    assert (out["repair_due"], out["exhaust_due"]) == ([], [7])


def test_derive_reports_missing_facts_as_unverifiable():
    out = derive_expected(start([ready_pull(age=None)]), NOW)
    assert out["unverifiable"] == ["whether pull request 7 needed a repair"]


@pytest.mark.parametrize(
    "reason",
    [
        "CI job `python` is `failure` on the head commit",
        "the Claude review of the head commit completed and found a problem: read the log",
        "a Codex finding on the head commit is a blocker (blocker, P0, or P1)",
        "Codex requested changes on the head commit",
        "`changes/005-x/report.md` is missing at the head commit",
        "`src/petasos/x.py` is outside the change's wall",
        BEHIND,
    ],
)
def test_repair_reasons_match_the_gate_wording(reason):
    assert builder_result.repair_reasons([reason]) == [reason]


@pytest.mark.parametrize(
    "reason",
    [
        "CI job `python` is `missing` on the head commit",
        "the Claude review of the head commit did not complete or was invalid",
        WAIT_CODEX,
        "the `hold` label is set",
    ],
)
def test_waiting_reasons_are_not_repair_reasons(reason):
    assert builder_result.repair_reasons([reason]) == []


def test_parse_change_reads_the_queue_lines():
    proposal = "# Proposal\n\nstatus: ratified\ndepends_on: 001-trust-core (uses it), 002-x\n"
    plan = "grounded_at: `b9d872b` (note)\nlane: claude\n"
    assert builder_result.parse_change("003-y", proposal, plan) == {
        "change": "003-y",
        "ratified": True,
        "depends_on": ["001-trust-core", "002-x"],
        "grounded": True,
        "lane_ok": True,
    }
    placeholder = "grounded_at: (set by the planning thread at ratification)\nlane: claude\n"
    assert (
        builder_result.parse_change("007-z", "depends_on: none\n", placeholder)["grounded"] is False
    )


# --- D3: the result's claims are compared with GitHub ----------------------------------------


def test_d3_blocked_with_the_wrong_head_fails():
    before = state([pull(7, draft=False)], expected=exp(exhaust_due=[7]))
    after = after_state([pull(7, draft=True, head=SHA_A)])
    problems = check(before, after, result("blocked", 7, CHANGE, SHA_B, "flagged"))
    assert any(f"not the {SHA_B} the builder reported" in p for p in problems)


def test_d3_blocked_on_a_draft_that_was_already_blocked_fails():
    before = state([pull(7, draft=True)])
    after = after_state([pull(7, draft=True)])
    problems = check(before, after, result("blocked", 7, CHANGE, SHA_A, "flagged"))
    assert any("already a draft at this head before the run" in p for p in problems)


def test_d3_noop_fails_when_an_existing_orphan_branch_moved():
    before = state(branches={BRANCH: SHA_A})
    after = after_state(branches={BRANCH: SHA_B})
    problems = check(before, after, noop("nothing_eligible"))
    assert problems == [f"The builder reported no-op, but branch {BRANCH} moved."]


def test_d3_started_from_the_wrong_branch_fails():
    wrong = pull(7)
    wrong["branch"] = "build/other"
    problems = check(START_ELIGIBLE, after_state([wrong]), result("started", 7, CHANGE, SHA_A))
    assert any(f"does not come from branch {BRANCH}" in p for p in problems)


# --- D4: the reported pull request is looked up whatever state it ended in -------------------


def merged_record(number=7, head=SHA_B, state_name="MERGED"):
    record = pull(number, head=head)
    record["state"] = state_name
    return record


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_d4_repaired_or_synced_then_merged_passes(action):
    before = state([pull(7, head=SHA_A)], expected=exp(repair_due=[7]))
    after = after_state(closed={"7": "MERGED"}, reported=merged_record())
    assert check(before, after, result(action, 7, CHANGE, SHA_B)) == []


def test_d4_started_then_merged_passes():
    after = after_state(reported=merged_record(head=SHA_A))
    assert check(START_ELIGIBLE, after, result("started", 7, CHANGE, SHA_A)) == []


def test_d4_merged_with_the_wrong_head_fails():
    before = state([pull(7, head=SHA_A)], expected=exp(repair_due=[7]))
    after = after_state(closed={"7": "MERGED"}, reported=merged_record(head=SHA_C))
    problems = check(before, after, result("repaired", 7, CHANGE, SHA_B))
    assert any(f"not the {SHA_B} the builder reported" in p for p in problems)


def test_d4_closed_without_merging_fails():
    before = state([pull(7, head=SHA_A)], expected=exp(repair_due=[7]))
    after = after_state(closed={"7": "CLOSED"}, reported=merged_record(state_name="CLOSED"))
    problems = check(before, after, result("repaired", 7, CHANGE, SHA_B))
    assert problems == ["Pull request 7 was closed without merging."]


def test_d4_a_merged_pull_request_is_never_a_block():
    after = after_state(reported=merged_record(head=SHA_A))
    problems = check(EMPTY, after, result("blocked", 7, CHANGE, SHA_A, "flagged"))
    assert any("not an open draft" in p for p in problems)


# --- D5: a builder job that did not succeed fails the check ----------------------------------


@pytest.mark.parametrize("outcome", ["failure", "cancelled", None])
def test_d5_a_builder_job_that_did_not_succeed_is_a_problem(outcome):
    problems = check(EMPTY, EMPTY_AFTER, noop("nothing_eligible"), outcome)
    assert len(problems) == 1
    assert problems[0].startswith(f"The builder job ended as `{outcome or 'unknown'}`")


def test_d5_a_timed_out_builder_still_gets_the_orphan_diagnosis():
    after = after_state(branches={BRANCH: SHA_A})
    problems = check(EMPTY, after, None, "failure")
    assert f"The builder claimed {BRANCH} but opened no pull request." in problems
    assert "The builder returned no structured result." in problems


# --- Snapshots and main() --------------------------------------------------------------------


def test_snapshot_keeps_only_build_pull_requests_and_build_branches():
    pull_rows = [
        {
            "number": 7,
            "title": f"[build] {CHANGE}",
            "headRefName": BRANCH,
            "headRefOid": SHA_A,
            "isDraft": False,
            "labels": [{"name": "hold"}],
        },
        {
            "number": 8,
            "title": "Plan 009",
            "headRefName": "plan/009",
            "headRefOid": SHA_B,
            "isDraft": False,
            "labels": [],
        },
        {
            "number": 9,
            "title": "[build] not a slug",
            "headRefName": "build/x",
            "headRefOid": SHA_B,
            "isDraft": False,
            "labels": [],
        },
    ]
    ref_rows = [
        {"ref": f"refs/heads/{BRANCH}", "sha": SHA_A},
        {"ref": "refs/heads/main", "sha": SHA_B},
    ]
    snap = build_snapshot(pull_rows, ref_rows)
    assert snap == {
        "pulls": [
            {
                "number": 7,
                "title": f"[build] {CHANGE}",
                "branch": BRANCH,
                "head": SHA_A,
                "draft": False,
                "labels": ["hold"],
            }
        ],
        "branches": {BRANCH: SHA_A},
    }


def gh_row(number=7, change=CHANGE, head=SHA_A, draft=False, state_name=None):
    row = {
        "number": number,
        "title": f"[build] {change}",
        "headRefName": f"build/{change}",
        "headRefOid": head,
        "isDraft": draft,
        "labels": [],
    }
    if state_name:
        row["state"] = state_name
    return row


class FakeGh:
    """Stands in for the `gh` command line tool; answers from fixed data."""

    def __init__(self, pulls=(), refs=(), merged=(), comments=None, commits=None, views=None):
        self.pulls = list(pulls)
        self.refs = list(refs)
        self.merged = list(merged)
        self.comments = comments or {}
        self.commits = commits or {}
        self.views = views or {}
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            if "merged" in args:
                return json.dumps([{"title": f"[build] {c}"} for c in self.merged])
            return json.dumps(self.pulls)
        if args[:2] == ["pr", "view"]:
            number = int(args[2])
            if "--jq" in args:
                return self.views.get(number, {}).get("state", "OPEN") + "\n"
            return json.dumps(self.views[number])
        path = args[2] if args[1] == "--paginate" else args[1]
        if "matching-refs" in path:
            return "\n".join(json.dumps(r) for r in self.refs)
        if "/issues/" in path:
            number = int(path.split("/issues/")[1].split("/")[0])
            return "\n".join(json.dumps(c) for c in self.comments.get(number, []))
        if "/pulls/" in path:
            return "\n".join(json.dumps({"m": m}) for m in self.commits.get("repairs", []))
        if "/commits/" in path:
            return OLD + "\n"
        raise AssertionError(f"unexpected gh call {args}")


def _env(**extra):
    env = {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "BUILD_JOB_RESULT": "success",
    }
    env.update({k: v for k, v in extra.items() if v is not None})
    return env


def _queue(tmp_path, ratified=True):
    folder = tmp_path / "changes" / "007-demo-app"
    folder.mkdir(parents=True)
    status = "ratified" if ratified else "proposed"
    (folder / "proposal.md").write_text(f"status: {status}\ndepends_on: none\n")
    (folder / "plan.md").write_text("grounded_at: `b4c3cf9`\nlane: claude\n")
    return tmp_path


def _snapshot(tmp_path, gh, ratified=True):
    out = tmp_path / "out.txt"
    root = _queue(tmp_path / "repo", ratified)
    code = builder_result.main(
        ["--snapshot-output"], _env(GITHUB_OUTPUT=str(out)), gh, root=root, now=NOW
    )
    assert code == 0
    line = out.read_text().strip()
    assert line.startswith("state=")
    return line[len("state=") :]


def test_main_snapshot_records_binding_queue_and_expected(tmp_path):
    record = json.loads(_snapshot(tmp_path, FakeGh()))
    assert record["schema"] == 2
    assert record["binding"] == {"repo": "owner/repo", "run_id": "123", "run_attempt": "1"}
    assert record["changes"][0]["change"] == "007-demo-app"
    assert record["expected"]["eligible"] == "007-demo-app"


def test_main_snapshot_reads_the_gate_status_and_repair_count(tmp_path):
    comment = {
        "login": "github-actions[bot]",
        "body": f"<!-- petasos-merge-gate -->\n**Merge gate** for head commit `{SHA_A}`: "
        f"waiting on\n\n- {BEHIND}",
        "updated": "2026-10-03T00:00:00Z",
    }
    gh = FakeGh([gh_row()], comments={7: [comment]}, commits={"repairs": ["repair: one"]})
    record = json.loads(_snapshot(tmp_path, gh, ratified=False))
    assert record["pulls"][0]["status_reasons"] == [BEHIND]
    assert record["pulls"][0]["repair_commits"] == 1
    assert record["expected"]["repair_due"] == [7]


def test_main_snapshot_fails_when_gh_fails(tmp_path, capsys):
    def broken(args):
        raise RuntimeError("gh pr failed: bad credentials")

    out = tmp_path / "out.txt"
    code = builder_result.main(
        ["--snapshot-output"], _env(GITHUB_OUTPUT=str(out)), broken, root=tmp_path, now=NOW
    )
    assert code == 1
    assert "Could not read the starting state" in capsys.readouterr().err


def test_main_without_the_run_binding_exits_1(capsys):
    code = builder_result.main(["--verify"], {"GITHUB_REPOSITORY": "owner/repo"}, FakeGh())
    assert code == 1
    assert "GITHUB_RUN_ID" in capsys.readouterr().err


def test_main_verify_passes_for_a_started_pull_request(tmp_path, capsys):
    before = _snapshot(tmp_path, FakeGh())
    after_gh = FakeGh(
        [gh_row(7, "007-demo-app")],
        [{"ref": "refs/heads/build/007-demo-app", "sha": SHA_A}],
        views={7: gh_row(7, "007-demo-app", state_name="OPEN")},
    )
    code = builder_result.main(
        ["--verify"],
        _env(BEFORE_STATE=before, RESULT=result("started", 7, "007-demo-app", SHA_A)),
        after_gh,
    )
    assert code == 0, capsys.readouterr().err
    assert any("--repo" in c and "owner/repo" in c for c in after_gh.calls)


def test_main_verify_fails_without_a_starting_record(capsys):
    code = builder_result.main(["--verify"], _env(RESULT=noop("nothing_eligible")), FakeGh())
    assert code == 1
    assert "no trusted starting record" in capsys.readouterr().err


def test_main_verify_fails_for_another_run(tmp_path, capsys):
    before = _snapshot(tmp_path, FakeGh(), ratified=False)
    env = _env(BEFORE_STATE=before, RESULT=noop("nothing_eligible"), GITHUB_RUN_ATTEMPT="2")
    assert builder_result.main(["--verify"], env, FakeGh()) == 1
    assert "another repository, run, or attempt" in capsys.readouterr().err


def test_d1_a_hostile_builder_cannot_rewrite_the_evidence(tmp_path, capsys, monkeypatch):
    """The reviewer's acceptance case: the builder replaces the checker and the snapshot it
    could reach in its own job, sets its own environment, and leaves a new orphan branch.
    The verify job reads none of that: its baseline is the snapshot job's output, its code is
    the trusted checkout, and its environment is set by the workflow. It still fails."""
    honest = _snapshot(tmp_path, FakeGh(), ratified=False)
    builder_dir = tmp_path / "builder-job"
    builder_dir.mkdir()
    (builder_dir / "builder_result.py").write_text("import sys\nsys.exit(0)\n")
    orphan = [{"ref": f"refs/heads/{BRANCH}", "sha": SHA_A}]
    forged = {"schema": 2, "pulls": [], "branches": {BRANCH: SHA_A}}
    (builder_dir / "builder-before.json").write_text(json.dumps(forged))
    monkeypatch.chdir(builder_dir)
    monkeypatch.setenv("BEFORE_STATE", json.dumps(forged))  # the builder's own environment
    env = _env(BEFORE_STATE=honest, RESULT=noop("nothing_eligible"))
    code = builder_result.main(["--verify"], env, FakeGh(refs=orphan))
    err = capsys.readouterr().err
    assert code == 1
    assert f"The builder claimed {BRANCH} but opened no pull request." in err


def test_d1_the_substituted_baseline_would_have_hidden_the_orphan():
    """The reviewer's reproduction: with the after-state passed off as the before-state, the
    orphan disappears. This is why the baseline must come from outside the builder's job."""
    after = after_state(branches={BRANCH: SHA_A})
    honest = state()
    forged = state(branches={BRANCH: SHA_A})
    assert check(honest, after, noop("nothing_eligible")) != []
    assert check(forged, after, noop("nothing_eligible")) == []


def test_closed_since_asks_only_about_pull_requests_that_left_the_open_list():
    calls = []

    def runner(args):
        calls.append(list(args))
        return "MERGED\n"

    before = {"pulls": [{"number": 7}, {"number": 8}]}
    after = {"pulls": [{"number": 8}]}
    assert builder_result.closed_since(before, after, "o/r", runner) == {"7": "MERGED"}
    assert len(calls) == 1 and calls[0][:3] == ["pr", "view", "7"]


# --- D1 and D5: the workflow keeps the evidence out of the builder's job ---------------------


def _jobs(text):
    """Split the workflow's `jobs:` block into {job name: its text}."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z_]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
JOBS = _jobs(WORKFLOW)


def test_workflow_has_three_jobs_and_no_default_permissions():
    assert list(JOBS) == ["snapshot", "build", "verify"]
    assert "\npermissions: {}\n" in WORKFLOW


def test_workflow_verify_runs_after_everything_on_its_own_runner():
    verify = JOBS["verify"]
    assert "needs: [snapshot, build]" in verify
    assert "if: always()" in verify
    assert "BEFORE_STATE: ${{ needs.snapshot.outputs.state }}" in verify
    assert "BUILD_JOB_RESULT: ${{ needs.build.result }}" in verify
    assert "--verify" in verify


@pytest.mark.parametrize("job", ["snapshot", "verify"])
def test_workflow_trusted_jobs_use_main_code_and_read_only_tokens(job):
    text = JOBS[job]
    assert "ref: ${{ github.sha }}" in text
    assert "persist-credentials: false" in text
    assert "write" not in text.split("steps:")[0]


def test_workflow_snapshot_refuses_any_branch_but_main():
    assert "if: github.ref != 'refs/heads/main'" in JOBS["snapshot"]


def test_workflow_passes_no_files_between_jobs():
    for marker in ("upload-artifact", "download-artifact", "RUNNER_TEMP", "cache@"):
        assert marker not in WORKFLOW


def test_workflow_build_job_cannot_touch_runs_or_artifacts():
    build = JOBS["build"]
    assert "actions: write" not in build
    assert "--snapshot" not in build and "--verify" not in build


def test_workflow_reserves_time_by_stopping_the_builder_step_first():
    build = JOBS["build"]
    job_limit = int(re.search(r"^    timeout-minutes: (\d+)$", build, re.MULTILINE).group(1))
    step_limit = int(re.search(r"^        timeout-minutes: (\d+)$", build, re.MULTILINE).group(1))
    assert step_limit < job_limit
    assert int(re.search(r"timeout-minutes: (\d+)", JOBS["verify"]).group(1)) <= 15
