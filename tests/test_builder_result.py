"""Tests for the builder result checker (`.github/scripts/builder_result.py`).

Everything that decides is `check(before, after, result)`, a pure function over two saved
states and the builder's raw result, so almost every test builds a state by hand and calls it.
`main()` is exercised against a fake `gh` runner and explicit environment dicts: no network,
no environment variables read, no real home folder. Files go only under pytest's `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / ".github/scripts/builder_result.py"
_SPEC = importlib.util.spec_from_file_location("builder_result", MODULE_PATH)
builder_result = importlib.util.module_from_spec(_SPEC)
sys.modules["builder_result"] = builder_result
_SPEC.loader.exec_module(builder_result)

check = builder_result.check
parse_result = builder_result.parse_result
build_snapshot = builder_result.build_snapshot

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
CHANGE = "005-deploy-fly-cloudflare"
BRANCH = f"build/{CHANGE}"
OTHER_CHANGE = "006-other"
OTHER_BRANCH = f"build/{OTHER_CHANGE}"


def pull(number, change=CHANGE, head=SHA_A, draft=False, labels=()):
    return {
        "number": number,
        "title": f"[build] {change}",
        "branch": f"build/{change}",
        "head": head,
        "draft": draft,
        "labels": list(labels),
    }


def state(pulls=(), branches=None):
    """Build a saved state. By default every pull request's branch exists at its head."""
    pulls = list(pulls)
    if branches is None:
        branches = {p["branch"]: p["head"] for p in pulls}
    return {"pulls": pulls, "branches": dict(branches)}


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


# --- Rule 1: missing, empty, unparseable, or schema-invalid result ---------------------------


@pytest.mark.parametrize("raw", [None, "", "   \n"])
def test_missing_or_empty_result_is_a_problem(raw):
    problems = check(EMPTY, EMPTY, raw)
    assert problems == ["The builder returned no structured result."]


def test_unparseable_result_is_a_problem():
    problems = check(EMPTY, EMPTY, "{not json")
    assert problems == ["The builder's structured result is not valid JSON."]


def test_result_that_is_not_an_object_is_a_problem():
    assert check(EMPTY, EMPTY, "[1, 2]") == [
        "The builder's structured result is not a JSON object."
    ]


def test_result_with_a_missing_key_is_a_problem():
    raw = json.dumps({"action": "no-op", "pull_request": None, "change": None, "head": None})
    problems = check(EMPTY, EMPTY, raw)
    assert len(problems) == 1
    assert "missing reason" in problems[0]


def test_result_with_an_extra_key_is_a_problem():
    body = json.loads(noop("nothing_eligible"))
    body["extra"] = 1
    problems = check(EMPTY, EMPTY, json.dumps(body))
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
    problems = check(EMPTY, EMPTY, json.dumps(body))
    assert len(problems) >= 1
    assert "invalid" in problems[0]


def test_a_valid_result_object_is_accepted_without_json_text():
    parsed, why = parse_result(json.loads(noop("nothing_eligible")))
    assert why is None
    assert parsed["action"] == "no-op"


# --- Rule 7: null exactly for no-op ----------------------------------------------------------


def test_noop_with_a_pull_request_number_is_a_problem():
    raw = result("no-op", pull_request=7, reason="nothing_eligible")
    problems = check(EMPTY, EMPTY, raw)
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
    problems = check(EMPTY, EMPTY, raw)
    assert len(problems) == 1
    assert "must not be null" in problems[0]


def test_noop_with_all_nulls_is_valid():
    assert check(EMPTY, EMPTY, noop("nothing_eligible")) == []


# --- Rule 2: a claimed branch needs a pull request (the 2026-10-02 failure) ------------------


def test_2026_10_02_started_with_a_made_up_pull_request_number_fails():
    before = state()
    after = state(branches={BRANCH: SHA_A})  # only the empty claim commit, no pull request
    problems = check(before, after, result("started", 99, CHANGE, SHA_A))
    assert f"The builder claimed {BRANCH} but opened no pull request." in problems
    assert any("no open pull request has that number" in p for p in problems)


def test_2026_10_02_missing_result_with_an_orphan_claim_branch_fails_both_ways():
    before = state()
    after = state(branches={BRANCH: SHA_A})
    problems = check(before, after, None)
    assert problems[0] == "The builder returned no structured result."
    assert f"The builder claimed {BRANCH} but opened no pull request." in problems
    assert len(problems) == 2


def test_orphan_claim_branch_fails_even_when_the_result_says_noop():
    before = state()
    after = state(branches={BRANCH: SHA_A})
    problems = check(before, after, noop("nothing_eligible"))
    assert f"The builder claimed {BRANCH} but opened no pull request." in problems


def test_claim_branch_with_its_pull_request_passes_rule_2():
    before = state()
    after = state([pull(7)])
    assert check(before, after, result("started", 7, CHANGE, SHA_A)) == []


def test_branch_that_already_existed_before_without_a_pull_request_is_not_a_new_claim():
    before = state(branches={BRANCH: SHA_A})
    after = state(branches={BRANCH: SHA_A})
    assert check(before, after, noop("nothing_eligible")) == []


# --- Rule 3: started -------------------------------------------------------------------------


def test_started_passes_when_the_new_pull_request_matches():
    after = state([pull(7)])
    assert check(EMPTY, after, result("started", 7, CHANGE, SHA_A)) == []


def test_started_fails_when_the_pull_request_does_not_exist():
    problems = check(EMPTY, EMPTY, result("started", 7, CHANGE, SHA_A))
    assert problems == [
        "The builder said it started pull request 7, but no open pull request has that number."
    ]


def test_started_fails_when_the_pull_request_already_existed():
    before = state([pull(7)])
    after = state([pull(7)])
    problems = check(before, after, result("started", 7, CHANGE, SHA_A))
    assert any("already existed before the run" in p for p in problems)


def test_started_fails_when_the_title_is_wrong():
    wrong = pull(7)
    wrong["title"] = "[build] 006-other"
    problems = check(EMPTY, state([wrong]), result("started", 7, CHANGE, SHA_A))
    assert any('is not titled "[build] 005-deploy-fly-cloudflare"' in p for p in problems)


def test_started_fails_when_the_head_is_not_the_reported_head():
    after = state([pull(7, head=SHA_B)])
    problems = check(EMPTY, after, result("started", 7, CHANGE, SHA_A))
    assert any("not the" in p and SHA_A in p for p in problems)


# --- Rule 4: repaired and synced -------------------------------------------------------------


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_passes_when_the_head_changed_to_the_reported_head(action):
    before = state([pull(7, head=SHA_A)])
    after = state([pull(7, head=SHA_B)])
    assert check(before, after, result(action, 7, CHANGE, SHA_B)) == []


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_head_did_not_change(action):
    before = state([pull(7, head=SHA_A)])
    after = state([pull(7, head=SHA_A)])
    problems = check(before, after, result(action, 7, CHANGE, SHA_A))
    assert any("head commit did not change" in p for p in problems)


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_head_is_not_the_reported_head(action):
    before = state([pull(7, head=SHA_A)])
    after = state([pull(7, head=SHA_B)])
    problems = check(before, after, result(action, 7, CHANGE, SHA_C))
    assert len(problems) == 1
    assert SHA_C in problems[0]


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_pull_request_did_not_exist_before(action):
    after = state([pull(7, head=SHA_B)])
    problems = check(EMPTY, after, result(action, 7, CHANGE, SHA_B))
    assert any("not open both before and after" in p for p in problems)


@pytest.mark.parametrize("action", ["repaired", "synced"])
def test_repair_or_sync_fails_when_the_pull_request_is_gone_after(action):
    before = state([pull(7, head=SHA_A)])
    problems = check(before, EMPTY, result(action, 7, CHANGE, SHA_B))
    assert any("not open both before and after" in p for p in problems)


# --- Rule 5: blocked -------------------------------------------------------------------------


def test_blocked_passes_when_the_pull_request_is_a_draft():
    after = state([pull(7, draft=True)])
    assert check(EMPTY, after, result("blocked", 7, CHANGE, SHA_A, "flagged")) == []


def test_blocked_fails_when_the_pull_request_is_not_a_draft():
    after = state([pull(7, draft=False)])
    problems = check(EMPTY, after, result("blocked", 7, CHANGE, SHA_A, "flagged"))
    assert problems == ["The builder said pull request 7 is blocked, but it is not a draft."]


def test_blocked_fails_when_the_pull_request_does_not_exist():
    problems = check(EMPTY, EMPTY, result("blocked", 7, CHANGE, SHA_A, "flagged"))
    assert any("no open pull request has that number" in p for p in problems)


# --- Rule 6: no-op ---------------------------------------------------------------------------


def test_noop_nothing_eligible_passes_with_no_build_pull_request():
    assert check(EMPTY, EMPTY, noop("nothing_eligible")) == []


def test_noop_nothing_eligible_passes_when_all_pull_requests_are_draft_or_held():
    pulls = [pull(7, draft=True), pull(8, OTHER_CHANGE, labels=["hold"])]
    assert check(state(pulls), state(pulls), noop("nothing_eligible")) == []


def test_noop_nothing_eligible_fails_when_a_ready_pull_request_is_open():
    pulls = [pull(7)]
    problems = check(state(pulls), state(pulls), noop("nothing_eligible"))
    assert len(problems) == 1
    assert "neither a draft nor held" in problems[0]


def test_noop_waiting_on_planning_passes_with_a_draft_pull_request():
    pulls = [pull(7, draft=True)]
    assert check(state(pulls), state(pulls), noop("waiting_on_planning")) == []


def test_noop_waiting_on_planning_fails_without_a_draft_pull_request():
    pulls = [pull(7)]
    problems = check(state(pulls), state(pulls), noop("waiting_on_planning"))
    assert any("no open build pull request is a draft" in p for p in problems)


def test_noop_held_passes_with_a_held_pull_request():
    pulls = [pull(7, labels=["hold", "other"])]
    assert check(state(pulls), state(pulls), noop("held")) == []


def test_noop_held_fails_without_the_hold_label():
    pulls = [pull(7, labels=["other"])]
    problems = check(state(pulls), state(pulls), noop("held"))
    assert any("hold label" in p for p in problems)


@pytest.mark.parametrize("reason", ["waiting_on_reviews", "repairs_exhausted"])
def test_noop_waiting_or_exhausted_passes_with_a_ready_pull_request(reason):
    pulls = [pull(7)]
    assert check(state(pulls), state(pulls), noop(reason)) == []


@pytest.mark.parametrize("reason", ["waiting_on_reviews", "repairs_exhausted"])
def test_noop_waiting_or_exhausted_fails_with_only_draft_or_no_pull_requests(reason):
    assert check(EMPTY, EMPTY, noop(reason)) != []
    drafts = [pull(7, draft=True)]
    assert check(state(drafts), state(drafts), noop(reason)) != []


def test_noop_with_an_unknown_reason_is_a_problem():
    problems = check(EMPTY, EMPTY, noop("tired"))
    assert problems == ['The no-op reason "tired" is not one of the allowed reasons.']


def test_noop_fails_when_an_open_pull_request_head_changed():
    before = state([pull(7, head=SHA_A)])
    after = state([pull(7, head=SHA_B)])
    problems = check(before, after, noop("waiting_on_reviews"))
    assert any("got a new head commit" in p for p in problems)


def test_noop_fails_when_a_pull_request_was_opened():
    after = state([pull(7)])
    problems = check(EMPTY, after, noop("waiting_on_reviews"))
    assert any("was opened during the run" in p for p in problems)


def test_noop_fails_when_a_branch_was_created():
    before = state()
    after = state([pull(7)], branches={BRANCH: SHA_A})
    problems = check(before, after, noop("waiting_on_reviews"))
    assert any(f"created branch {BRANCH}" in p for p in problems)


def test_noop_allows_deleting_a_stale_claim_branch_with_no_pull_request():
    before = state([pull(7)], branches={BRANCH: SHA_A, OTHER_BRANCH: SHA_B})
    after = state([pull(7)], branches={BRANCH: SHA_A})
    assert check(before, after, noop("waiting_on_reviews")) == []


def test_noop_fails_when_a_branch_with_an_open_pull_request_was_deleted():
    before = state([pull(7), pull(8, OTHER_CHANGE)])
    after = state([pull(7)])
    problems = check(before, after, noop("waiting_on_reviews"))
    assert any(f"deleted branch {OTHER_BRANCH}" in p for p in problems)


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


class FakeGh:
    """Stands in for the `gh` command line tool; answers from fixed text."""

    def __init__(self, pulls, refs):
        self.pulls = pulls
        self.refs = refs
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "pr":
            return json.dumps(self.pulls)
        # `gh api --paginate --jq '.[] | ...'` prints one JSON value per line.
        return "\n".join(json.dumps(r) for r in self.refs)


def _env(raw=None):
    env = {"GITHUB_REPOSITORY": "owner/repo"}
    if raw is not None:
        env["RESULT"] = raw
    return env


def test_main_snapshot_then_check_passes_for_a_started_pull_request(tmp_path, capsys):
    before_file = tmp_path / "before.json"
    assert builder_result.main(["--snapshot", str(before_file)], _env(), FakeGh([], [])) == 0
    assert json.loads(before_file.read_text()) == {"pulls": [], "branches": {}}

    after_gh = FakeGh(
        [
            {
                "number": 7,
                "title": f"[build] {CHANGE}",
                "headRefName": BRANCH,
                "headRefOid": SHA_A,
                "isDraft": False,
                "labels": [],
            }
        ],
        [{"ref": f"refs/heads/{BRANCH}", "sha": SHA_A}],
    )
    code = builder_result.main(
        ["--before", str(before_file), "--result-env", "RESULT"],
        _env(result("started", 7, CHANGE, SHA_A)),
        after_gh,
    )
    assert code == 0
    assert "matches the repository" in capsys.readouterr().out
    assert any("--repo" in c and "owner/repo" in c for c in after_gh.calls)


def test_main_exits_1_and_prints_a_sentence_for_the_2026_10_02_case(tmp_path, capsys):
    before_file = tmp_path / "before.json"
    before_file.write_text(json.dumps(EMPTY))
    gh = FakeGh([], [{"ref": f"refs/heads/{BRANCH}", "sha": SHA_A}])
    code = builder_result.main(["--before", str(before_file), "--result-env", "RESULT"], _env(), gh)
    err = capsys.readouterr().err
    assert code == 1
    assert f"The builder claimed {BRANCH} but opened no pull request." in err
    assert "The builder returned no structured result." in err


def test_main_exits_1_when_the_before_file_is_missing(tmp_path, capsys):
    code = builder_result.main(
        ["--before", str(tmp_path / "nope.json"), "--result-env", "RESULT"],
        _env(noop("nothing_eligible")),
        FakeGh([], []),
    )
    assert code == 1
    assert "Could not compare" in capsys.readouterr().err


def test_main_exits_1_when_gh_fails(tmp_path, capsys):
    def broken(args):
        raise RuntimeError("gh pr failed: bad credentials")

    code = builder_result.main(["--snapshot", str(tmp_path / "b.json")], _env(), broken)
    assert code == 1
    assert "Could not read the repository state" in capsys.readouterr().err


def test_main_without_a_repository_name_exits_1(tmp_path, capsys):
    code = builder_result.main(["--snapshot", str(tmp_path / "b.json")], {}, FakeGh([], []))
    assert code == 1
    assert "GITHUB_REPOSITORY" in capsys.readouterr().err


def test_no_op_tolerates_the_gate_merging_a_pull_request_during_the_run():
    pull = {
        "number": 40,
        "title": "[build] 006-readme-transcript-site",
        "branch": "build/006-readme-transcript-site",
        "head": "a" * 40,
        "draft": False,
        "labels": [],
    }
    before = {"pulls": [pull], "branches": {"build/006-readme-transcript-site": "a" * 40}}
    after = {"pulls": [], "branches": {}, "closed": {"40": "MERGED"}}
    result = {
        "action": "no-op",
        "pull_request": None,
        "change": None,
        "head": None,
        "reason": "nothing_eligible",
    }
    assert builder_result.check(before, after, result) == []


def test_no_op_still_flags_a_deleted_branch_whose_pull_request_was_closed_not_merged():
    pull = {
        "number": 41,
        "title": "[build] 006-readme-transcript-site",
        "branch": "build/006-readme-transcript-site",
        "head": "b" * 40,
        "draft": False,
        "labels": [],
    }
    before = {"pulls": [pull], "branches": {"build/006-readme-transcript-site": "b" * 40}}
    after = {"pulls": [], "branches": {}, "closed": {"41": "CLOSED"}}
    result = {
        "action": "no-op",
        "pull_request": None,
        "change": None,
        "head": None,
        "reason": "nothing_eligible",
    }
    assert any("deleted branch" in p for p in builder_result.check(before, after, result))


def test_closed_since_asks_only_about_pull_requests_that_left_the_open_list():
    calls = []

    def runner(args):
        calls.append(list(args))
        return "MERGED\n"

    before = {"pulls": [{"number": 7}, {"number": 8}]}
    after = {"pulls": [{"number": 8}]}
    assert builder_result.closed_since(before, after, "o/r", runner) == {"7": "MERGED"}
    assert len(calls) == 1 and calls[0][:3] == ["pr", "view", "7"]
