"""Unit tests for the merge gate's pure decision logic.

Loads `.github/scripts/merge_gate.py` by path (it lives outside any package) and exercises
`evaluate`, `parse_wall_expected`, `glob_to_regex`/`matches_any`, `is_blocker_text`, and
`should_request_codex` directly. No network, no environment variables, no files written, no
real clock: `main`, `gather`, and `GitHub` are never called.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / ".github/scripts/merge_gate.py"
SPEC = importlib.util.spec_from_file_location("merge_gate", MODULE_PATH)
merge_gate = importlib.util.module_from_spec(SPEC)
sys.modules["merge_gate"] = merge_gate  # dataclasses need the module registered before exec
SPEC.loader.exec_module(merge_gate)

PullFacts = merge_gate.PullFacts
Comment = merge_gate.Comment
Review = merge_gate.Review
Reaction = merge_gate.Reaction
evaluate = merge_gate.evaluate
parse_wall_expected = merge_gate.parse_wall_expected
glob_to_regex = merge_gate.glob_to_regex
matches_any = merge_gate.matches_any
is_blocker_text = merge_gate.is_blocker_text
should_request_codex = merge_gate.should_request_codex
codex_request_body = merge_gate.codex_request_body

SHA = "a" * 40
OLD_SHA = "b" * 40
CLAUDE_LOGIN = "claude[bot]"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"

DEFAULT_PLAN_TEXT = (
    "wall_expected:\n"
    "- `src/petasos/trust/**`\n"
    "- `tests/test_trust_*.py`\n"
    "- `changes/001-trust-core/tasks.md` (ticking boxes only)\n"
    "\n"
    "The wall list above is what the builder promised to touch.\n"
)

DEFAULT_CHANGED_FILES = [
    "src/petasos/trust/grants.py",
    "tests/test_trust_grants.py",
    "changes/001-trust-core/report.md",
    "changes/001-trust-core/tasks.md",
]


def ready_facts(**overrides) -> PullFacts:
    """A PullFacts that satisfies every rule in evaluate(); overrides tweak one field at a time."""
    facts = PullFacts(
        number=1,
        title="[build] 001-trust-core",
        draft=False,
        base_ref="main",
        head_ref="build/001-trust-core",
        head_sha=SHA,
        head_repo_is_base_repo=True,
        labels=[],
        body="## Wall check\n\nPASS\n",
        changed_files=list(DEFAULT_CHANGED_FILES),
        check_conclusions={
            "python": "success",
            "demo": "success",
            "private-identifiers": "success",
        },
        ci_started_at="2026-09-26T10:00:00Z",
        issue_comments=[
            Comment(
                author=CLAUDE_LOGIN,
                body=f"Petasos review: claude\nCommit: {SHA}\nBlockers: 0\n",
            )
        ],
        inline_comments=[],
        reviews=[Review(author=CODEX_LOGIN, state="COMMENTED", body="Looks fine.", commit_id=SHA)],
        reactions=[],
        report_text="# Report: 001-trust-core\n\nVerdict: BUILT\nSummary: done.\n",
        plan_text=DEFAULT_PLAN_TEXT,
    )
    return dataclasses.replace(facts, **overrides)


def reasons_contain(verdict, substring: str) -> bool:
    return any(substring in r for r in verdict.reasons)


# --------------------------------------------------------------------------------------- ready


def test_ready_facts_is_ready_with_no_reasons():
    verdict = evaluate(ready_facts())
    assert verdict.ready is True
    assert verdict.change == "001-trust-core"
    assert verdict.reasons == ()


# ----------------------------------------------------------------------------------- rule 1: PR


def test_non_build_title_is_not_ready_and_has_no_change():
    verdict = evaluate(ready_facts(title="Fix the thing"))
    assert verdict.ready is False
    assert verdict.change is None
    assert verdict.reasons == ("title is not `[build] NNN-slug`",)


def test_draft_pull_request_is_not_ready():
    verdict = evaluate(ready_facts(draft=True))
    assert verdict.ready is False
    assert reasons_contain(verdict, "pull request is a draft")


def test_wrong_base_branch_is_not_ready():
    verdict = evaluate(ready_facts(base_ref="develop"))
    assert verdict.ready is False
    assert reasons_contain(verdict, "base is `develop`, not `main`")


def test_wrong_head_branch_is_not_ready():
    verdict = evaluate(ready_facts(head_ref="build/999-other-thing"))
    assert verdict.ready is False
    assert reasons_contain(
        verdict, "head branch is `build/999-other-thing`, expected `build/001-trust-core`"
    )


def test_fork_head_is_not_ready():
    verdict = evaluate(ready_facts(head_repo_is_base_repo=False))
    assert verdict.ready is False
    assert reasons_contain(verdict, "head branch is in another repository")


def test_hold_label_is_not_ready():
    verdict = evaluate(ready_facts(labels=["hold"]))
    assert verdict.ready is False
    assert reasons_contain(verdict, "the `hold` label is set")


# ---------------------------------------------------------------------------------- rule 2: CI


@pytest.mark.parametrize("check_name", ["python", "demo", "private-identifiers"])
def test_ci_job_failure_is_not_ready(check_name):
    conclusions = {"python": "success", "demo": "success", "private-identifiers": "success"}
    conclusions[check_name] = "failure"
    verdict = evaluate(ready_facts(check_conclusions=conclusions))
    assert verdict.ready is False
    assert reasons_contain(verdict, f"CI job `{check_name}` is `failure` on the head commit")


@pytest.mark.parametrize("check_name", ["python", "demo", "private-identifiers"])
def test_ci_job_missing_is_not_ready(check_name):
    conclusions = {"python": "success", "demo": "success", "private-identifiers": "success"}
    del conclusions[check_name]
    verdict = evaluate(ready_facts(check_conclusions=conclusions))
    assert verdict.ready is False
    assert reasons_contain(verdict, f"CI job `{check_name}` is `missing` on the head commit")


# ---------------------------------------------------------------------- rule 3: Claude review


def test_missing_claude_summary_is_not_ready():
    verdict = evaluate(ready_facts(issue_comments=[]))
    assert verdict.ready is False
    assert reasons_contain(verdict, "no Claude review summary for the head commit")


def test_claude_summary_for_a_different_sha_is_not_ready():
    verdict = evaluate(
        ready_facts(
            issue_comments=[
                Comment(
                    author=CLAUDE_LOGIN,
                    body=f"Petasos review: claude\nCommit: {OLD_SHA}\nBlockers: 0\n",
                )
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "no Claude review summary for the head commit")


def test_claude_summary_reporting_blockers_is_not_ready():
    verdict = evaluate(
        ready_facts(
            issue_comments=[
                Comment(
                    author=CLAUDE_LOGIN,
                    body=f"Petasos review: claude\nCommit: {SHA}\nBlockers: 1\n",
                )
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(
        verdict, "the Claude review summary for the head commit reports blockers"
    )


def test_claude_summary_by_non_claude_author_is_not_ready():
    verdict = evaluate(
        ready_facts(
            issue_comments=[
                Comment(
                    author="not-claude[bot]",
                    body=f"Petasos review: claude\nCommit: {SHA}\nBlockers: 0\n",
                )
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "no Claude review summary for the head commit")


def test_claude_inline_blocker_on_head_sha_is_not_ready():
    verdict = evaluate(
        ready_facts(
            inline_comments=[
                Comment(
                    author=CLAUDE_LOGIN, body="**blocker** | src/x.py:3 | fix this", commit_id=SHA
                )
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "a Claude inline finding on the head commit is a blocker")


def test_claude_inline_blocker_on_old_sha_does_not_block():
    verdict = evaluate(
        ready_facts(
            inline_comments=[
                Comment(
                    author=CLAUDE_LOGIN,
                    body="**blocker** | src/x.py:3 | fix this",
                    commit_id=OLD_SHA,
                )
            ]
        )
    )
    assert verdict.ready is True


def test_claude_changes_requested_on_head_is_not_ready():
    verdict = evaluate(
        ready_facts(
            reviews=[
                Review(author=CODEX_LOGIN, state="COMMENTED", body="Looks fine.", commit_id=SHA),
                Review(author=CLAUDE_LOGIN, state="CHANGES_REQUESTED", body="", commit_id=SHA),
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "Claude requested changes on the head commit")


# ----------------------------------------------------------------------- rule 4: Codex review


def test_missing_codex_review_is_not_ready():
    verdict = evaluate(ready_facts(reviews=[]))
    assert verdict.ready is False
    assert reasons_contain(verdict, "no Codex review for the head commit")


def test_codex_review_on_old_sha_only_is_not_ready():
    verdict = evaluate(
        ready_facts(
            reviews=[Review(author=CODEX_LOGIN, state="COMMENTED", body="fine", commit_id=OLD_SHA)]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "no Codex review for the head commit")


def test_codex_thumbs_up_after_ci_started_counts_as_a_review():
    verdict = evaluate(
        ready_facts(
            reviews=[],
            reactions=[
                Reaction(author=CODEX_LOGIN, content="+1", created_at="2026-09-26T10:05:00Z")
            ],
        )
    )
    assert verdict.ready is True


def test_codex_thumbs_up_before_ci_started_does_not_count():
    verdict = evaluate(
        ready_facts(
            reviews=[],
            reactions=[
                Reaction(author=CODEX_LOGIN, content="+1", created_at="2026-09-26T09:59:00Z")
            ],
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "no Codex review for the head commit")


def test_codex_thumbs_up_by_someone_else_does_not_count():
    verdict = evaluate(
        ready_facts(
            reviews=[],
            reactions=[
                Reaction(author="random-user", content="+1", created_at="2026-09-26T10:05:00Z")
            ],
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "no Codex review for the head commit")


def test_codex_body_with_p1_badge_blocks():
    verdict = evaluate(
        ready_facts(
            reviews=[
                Review(
                    author=CODEX_LOGIN,
                    state="COMMENTED",
                    body="Found an issue [P1] please fix.",
                    commit_id=SHA,
                )
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(
        verdict, "a Codex finding on the head commit is a blocker (blocker, P0, or P1)"
    )


def test_codex_body_with_p0_badge_blocks():
    verdict = evaluate(
        ready_facts(
            reviews=[
                Review(
                    author=CODEX_LOGIN,
                    state="COMMENTED",
                    body="This is a P0 issue.",
                    commit_id=SHA,
                )
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(
        verdict, "a Codex finding on the head commit is a blocker (blocker, P0, or P1)"
    )


def test_codex_inline_comment_with_p1_on_head_sha_blocks():
    verdict = evaluate(
        ready_facts(
            inline_comments=[
                Comment(author=CODEX_LOGIN, body="flag: P1 concern here", commit_id=SHA)
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(
        verdict, "a Codex finding on the head commit is a blocker (blocker, P0, or P1)"
    )


def test_codex_changes_requested_blocks():
    verdict = evaluate(
        ready_facts(
            reviews=[
                Review(author=CODEX_LOGIN, state="CHANGES_REQUESTED", body="fine", commit_id=SHA)
            ]
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "Codex requested changes on the head commit")


# ------------------------------------------------------------------------- rule 5: the report


def test_missing_report_is_not_ready():
    verdict = evaluate(ready_facts(report_text=None))
    assert verdict.ready is False
    assert reasons_contain(
        verdict, "`changes/001-trust-core/report.md` is missing at the head commit"
    )


def test_verdict_built_with_flags_is_not_ready():
    verdict = evaluate(
        ready_facts(report_text="# Report\n\nVerdict: BUILT WITH FLAGS\nSummary: done.\n")
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "the report's verdict is not exactly `Verdict: BUILT`")


def test_verdict_blocked_is_not_ready():
    verdict = evaluate(ready_facts(report_text="# Report\n\nVerdict: BLOCKED\nSummary: no.\n"))
    assert verdict.ready is False
    assert reasons_contain(verdict, "the report's verdict is not exactly `Verdict: BUILT`")


@pytest.mark.parametrize("placeholder", ["TODO", "TBD", "<!--", "[fill"])
def test_report_with_placeholder_is_not_ready(placeholder):
    verdict = evaluate(
        ready_facts(
            report_text=f"# Report\n\nVerdict: BUILT\nSummary: done. {placeholder} more later\n"
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "the report still has a placeholder")


# --------------------------------------------------------------------- rule 6: PR body wall


def test_pr_body_without_wall_section_is_not_ready():
    verdict = evaluate(ready_facts(body="Just a description, no wall check here."))
    assert verdict.ready is False
    assert reasons_contain(verdict, "the pull request body has no `## Wall check` section")


# --------------------------------------------------------------------- rule 7: the gate's wall


def test_missing_plan_is_not_ready():
    verdict = evaluate(ready_facts(plan_text=None))
    assert verdict.ready is False
    assert reasons_contain(verdict, "`changes/001-trust-core/plan.md` is missing on `main`")


def test_plan_with_no_wall_expected_entries_is_not_ready():
    verdict = evaluate(ready_facts(plan_text="wall_expected:\n\nNothing listed above.\n"))
    assert verdict.ready is False
    assert reasons_contain(verdict, "the plan's `wall_expected` list is empty or unreadable")


def test_changed_file_outside_wall_is_not_ready():
    verdict = evaluate(
        ready_facts(changed_files=DEFAULT_CHANGED_FILES + ["src/other/unrelated.py"])
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, "`src/other/unrelated.py` is outside the change's wall")


STANDING_FORBIDDEN_PATHS = [
    "AGENTS.md",
    "DECISIONS.md",
    ".github/workflows/ci.yml",
    ".github/scripts/merge_gate.py",
    "changes/001-trust-core/spec.md",
    "changes/001-trust-core/plan.md",
    "changes/002-memory-canary-guard/proposal.md",
    "tests/test_no_private_identifiers.py",
    "tests/test_merge_gate.py",
    "review/x.md",
]


@pytest.mark.parametrize("forbidden_path", STANDING_FORBIDDEN_PATHS)
def test_standing_forbidden_file_is_not_ready_even_if_in_wall(forbidden_path):
    plan_text = (
        "wall_expected:\n"
        "- `src/petasos/trust/**`\n"
        "- `tests/test_trust_*.py`\n"
        "- `changes/001-trust-core/tasks.md` (ticking boxes only)\n"
        f"- `{forbidden_path}`\n"
        "\n"
        "More notes below the wall list.\n"
    )
    verdict = evaluate(
        ready_facts(
            plan_text=plan_text,
            changed_files=DEFAULT_CHANGED_FILES + [forbidden_path],
        )
    )
    assert verdict.ready is False
    assert reasons_contain(verdict, f"`{forbidden_path}` is standing-forbidden")


def test_questions_file_is_allowed():
    verdict = evaluate(ready_facts(changed_files=DEFAULT_CHANGED_FILES + ["changes/QUESTIONS.md"]))
    assert verdict.ready is True


def test_no_changed_files_is_not_ready():
    verdict = evaluate(ready_facts(changed_files=[]))
    assert verdict.ready is False
    assert reasons_contain(verdict, "the pull request changes no files")


# ------------------------------------------------------------------------------ is_blocker_text


@pytest.mark.parametrize(
    "text",
    [
        "blocker | a | b",
        "**blocker** | x",
        "- blocker: x",
        "BLOCKER | x",
        "[blocker] | x",
    ],
)
def test_is_blocker_text_true_for_blocker_shapes(text):
    assert is_blocker_text(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "should-fix | x",
        "nit | x",
        "Blockers: 0",
        "this is not a blocker because",
        "unblocker | x",
    ],
)
def test_is_blocker_text_false_for_non_blocker_shapes(text):
    assert is_blocker_text(text) is False


# ------------------------------------------------------------------------------------- globbing


def test_double_star_crosses_folders():
    assert matches_any("src/petasos/trust/a/b.py", ["src/petasos/trust/**"])


def test_single_star_stays_in_one_folder():
    assert matches_any("tests/test_trust_x.py", ["tests/test_trust_*.py"])
    assert not matches_any("tests/sub/test_trust_x.py", ["tests/test_trust_*.py"])


def test_single_star_segment_does_not_match_missing_segment():
    assert not matches_any("changes/spec.md", ["changes/*/spec.md"])


def test_glob_to_regex_full_match_only():
    pattern = glob_to_regex("tests/test_trust_*.py")
    assert pattern.match("tests/test_trust_x.py")
    assert not pattern.match("prefix/tests/test_trust_x.py")


# ------------------------------------------------------------------------- parse_wall_expected


def test_parse_wall_expected_ignores_annotation_and_stops_at_blank_line():
    patterns = parse_wall_expected(DEFAULT_PLAN_TEXT)
    assert patterns == [
        "src/petasos/trust/**",
        "tests/test_trust_*.py",
        "changes/001-trust-core/tasks.md",
    ]


def test_parse_wall_expected_ignores_entries_after_blank_line():
    plan_text = "wall_expected:\n- `src/a/**`\n\n- `src/should/not/appear.md`\n"
    assert parse_wall_expected(plan_text) == ["src/a/**"]


# -------------------------------------------------------------------------- should_request_codex


def test_should_request_codex_true_when_only_codex_missing_and_ci_green():
    facts = ready_facts(reviews=[])
    verdict = evaluate(facts)
    assert should_request_codex(facts, verdict) is True


def test_should_request_codex_false_when_ci_not_green():
    facts = ready_facts(
        reviews=[],
        check_conclusions={
            "python": "failure",
            "demo": "success",
            "private-identifiers": "success",
        },
    )
    verdict = evaluate(facts)
    assert should_request_codex(facts, verdict) is False


def test_should_request_codex_false_when_request_comment_already_exists():
    facts = ready_facts(
        reviews=[],
        issue_comments=[
            Comment(
                author=CLAUDE_LOGIN, body=f"Petasos review: claude\nCommit: {SHA}\nBlockers: 0\n"
            ),
            Comment(author="someone", body=codex_request_body(SHA)),
        ],
    )
    verdict = evaluate(facts)
    assert should_request_codex(facts, verdict) is False


def test_should_request_codex_true_again_for_a_new_sha():
    new_sha = "c" * 40
    facts = ready_facts(
        reviews=[],
        head_sha=new_sha,
        issue_comments=[
            Comment(
                author=CLAUDE_LOGIN,
                body=f"Petasos review: claude\nCommit: {new_sha}\nBlockers: 0\n",
            ),
            Comment(author="someone", body=codex_request_body(SHA)),
        ],
    )
    verdict = evaluate(facts)
    assert should_request_codex(facts, verdict) is True
