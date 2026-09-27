"""Tests for the merge gate (version 2, `.github/scripts/merge_gate.py`).

Loads the module by path with importlib since it lives outside any package. Everything that
decides is `evaluate()`, a pure function over `PullFacts`, so almost every test here calls it
directly with a hand-built, always-ready `PullFacts` (`ready_facts()`) and one override per
rule. `main()` and `gather()` are exercised too, but only against fake `GitHub` objects built
in this file: no network, no environment variables read outside the explicit `patch.dict`
blocks below, no files written, no real clock.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / ".github/scripts/merge_gate.py"
_SPEC = importlib.util.spec_from_file_location("merge_gate", MODULE_PATH)
merge_gate = importlib.util.module_from_spec(_SPEC)
sys.modules["merge_gate"] = merge_gate
_SPEC.loader.exec_module(merge_gate)

FileChange = merge_gate.FileChange
Comment = merge_gate.Comment
Review = merge_gate.Review
WorkflowEvidence = merge_gate.WorkflowEvidence
PullFacts = merge_gate.PullFacts
evaluate = merge_gate.evaluate
gather = merge_gate.gather
glob_to_regex = merge_gate.glob_to_regex
matches_any = merge_gate.matches_any
parse_wall_expected = merge_gate.parse_wall_expected
parse_wall_forbidden = merge_gate.parse_wall_forbidden
parse_depends_on = merge_gate.parse_depends_on
only_ticks = merge_gate.only_ticks
only_appends = merge_gate.only_appends
is_blocker_text = merge_gate.is_blocker_text
should_request_codex = merge_gate.should_request_codex
codex_request_body = merge_gate.codex_request_body
STATUS_MARKER = merge_gate.STATUS_MARKER

SHA = "a" * 40
OLD_SHA = "b" * 40
MAIN_SHA = "m" * 40
MERGE_SHA = "c" * 40

PLAN_TEXT = (
    "grounded_at: `1234567`\n"
    "\n"
    "lane: claude\n"
    "\n"
    "wall_expected:\n"
    "- `src/petasos/trust/**`\n"
    "- `tests/test_trust_*.py`\n"
    "- `changes/001-trust-core/tasks.md` (ticking boxes only)\n"
    "\n"
    "wall_forbidden: everything standing-forbidden. Do not touch `src/petasos/mcp/**` or "
    "`src/petasos/__init__.py`.\n"
    "\n"
    "More prose.\n"
)

PROPOSAL_TEXT = "status: ratified\nlane: claude\ndepends_on: 000-bootstrap\n"

REPORT_TEXT = "# Report\n\nVerdict: BUILT\n"

BODY_TEXT = "## Wall check\n\nPASS\n"

TASKS_PATH = "changes/001-trust-core/tasks.md"
TICK_ONLY_PATCH = "@@ -1,2 +1,2 @@\n-- [ ] Write x\n+- [x] Write x\n"


def default_files() -> list[FileChange]:
    return [
        FileChange("src/petasos/trust/grants.py", "modified"),
        FileChange("tests/test_trust_grants.py", "added"),
        FileChange("changes/001-trust-core/report.md", "added"),
        FileChange(TASKS_PATH, "modified", patch=TICK_ONLY_PATCH),
    ]


def ready_facts(**overrides) -> PullFacts:
    defaults = {
        "number": 1,
        "title": "[build] 001-trust-core",
        "draft": False,
        "base_ref": "main",
        "head_ref": "build/001-trust-core",
        "head_sha": SHA,
        "head_repo_is_base_repo": True,
        "labels": [],
        "body": BODY_TEXT,
        "files": default_files(),
        "files_complete": True,
        "ci": WorkflowEvidence(
            ".github/workflows/ci.yml",
            "pull_request",
            SHA,
            "completed",
            {"python": "success", "demo": "success", "private-identifiers": "success"},
        ),
        "claude_review": WorkflowEvidence(
            ".github/workflows/claude-review.yml",
            "pull_request",
            SHA,
            "completed",
            {"review": "success"},
        ),
        "reviews": [Review("chatgpt-codex-connector[bot]", "COMMENTED", "Looks fine.", SHA)],
        "inline_comments": [],
        "codex_thumbs_on_request": False,
        "report_text": REPORT_TEXT,
        "plan_text": PLAN_TEXT,
        "proposal_text": PROPOSAL_TEXT,
        "merged_changes": frozenset({"000-bootstrap"}),
        "grounded_in_main": True,
        "main_changed_since_grounding": [],
        "main_changed_since_branch": [],
    }
    defaults.update(overrides)
    return PullFacts(**defaults)


def assert_not_ready(facts: PullFacts, substring: str) -> None:
    verdict = evaluate(facts)
    assert not verdict.ready, "expected not ready"
    assert any(substring in r for r in verdict.reasons), verdict.reasons


# ---------------------------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------------------------


def test_ready_facts_is_ready_with_no_reasons():
    verdict = evaluate(ready_facts())
    assert verdict.ready
    assert verdict.change == "001-trust-core"
    assert verdict.reasons == ()


# ---------------------------------------------------------------------------------------------
# Shape rules
# ---------------------------------------------------------------------------------------------


def test_non_build_title_yields_no_change():
    verdict = evaluate(ready_facts(title="Fix a typo"))
    assert not verdict.ready
    assert verdict.change is None
    assert "title is not" in verdict.reasons[0]


def test_draft_pull_request_not_ready():
    assert_not_ready(ready_facts(draft=True), "draft")


def test_wrong_base_not_ready():
    assert_not_ready(ready_facts(base_ref="develop"), "base is `develop`")


def test_wrong_head_branch_not_ready():
    assert_not_ready(ready_facts(head_ref="build/002-other"), "head branch is `build/002-other`")


def test_fork_head_repo_not_ready():
    assert_not_ready(ready_facts(head_repo_is_base_repo=False), "another repository")


def test_hold_label_not_ready():
    assert_not_ready(ready_facts(labels=["hold"]), "`hold` label")


# ---------------------------------------------------------------------------------------------
# Proposal: ratified, depends_on, merged dependencies
# ---------------------------------------------------------------------------------------------


def test_missing_proposal_not_ready():
    assert_not_ready(ready_facts(proposal_text=None), "proposal.md` is missing on `main`")


def test_proposal_not_ratified_not_ready():
    assert_not_ready(
        ready_facts(proposal_text="status: proposed\ndepends_on: none\n"),
        "not `status: ratified`",
    )


def test_proposal_missing_depends_on_line_not_ready():
    assert_not_ready(ready_facts(proposal_text="status: ratified\n"), "no `depends_on:` line")


def test_unmerged_dependency_not_ready():
    assert_not_ready(ready_facts(merged_changes=frozenset()), "dependency `000-bootstrap`")


def test_depends_on_none_with_empty_merged_set_is_ready():
    facts = ready_facts(
        proposal_text="status: ratified\ndepends_on: none\n",
        merged_changes=frozenset(),
    )
    assert evaluate(facts).ready


# ---------------------------------------------------------------------------------------------
# Plan: grounded_at, grounded_in_main, drift since grounding
# ---------------------------------------------------------------------------------------------


def test_plan_without_grounded_at_not_ready():
    plan = PLAN_TEXT.replace("grounded_at: `1234567`\n\n", "")
    assert_not_ready(ready_facts(plan_text=plan), "no `grounded_at` commit")


def test_grounded_in_main_false_not_ready():
    assert_not_ready(
        ready_facts(grounded_in_main=False), "does not contain the plan's `grounded_at`"
    )


def test_grounded_in_main_none_not_ready():
    assert_not_ready(
        ready_facts(grounded_in_main=None), "does not contain the plan's `grounded_at`"
    )


def test_main_changed_since_grounding_unknown_not_ready():
    assert_not_ready(
        ready_facts(main_changed_since_grounding=None),
        "could not list what `main` changed since `grounded_at`",
    )


def test_main_changed_since_grounding_in_wall_not_ready():
    assert_not_ready(
        ready_facts(main_changed_since_grounding=["src/petasos/trust/x.py"]),
        "`main` changed `src/petasos/trust/x.py`",
    )


def test_main_changed_since_grounding_unrelated_path_stays_ready():
    facts = ready_facts(main_changed_since_grounding=["docs/a.md"])
    assert evaluate(facts).ready


# ---------------------------------------------------------------------------------------------
# Current with main since the branch was cut
# ---------------------------------------------------------------------------------------------


def test_main_changed_since_branch_unknown_not_ready():
    assert_not_ready(
        ready_facts(main_changed_since_branch=None),
        "could not list what `main` changed since this branch was cut",
    )


def test_main_changed_since_branch_touched_file_not_ready():
    assert_not_ready(
        ready_facts(main_changed_since_branch=["src/petasos/trust/grants.py"]),
        "`main` changed `src/petasos/trust/grants.py`",
    )


def test_main_changed_since_branch_wall_path_not_ready():
    assert_not_ready(
        ready_facts(main_changed_since_branch=["src/petasos/trust/untouched.py"]),
        "`main` changed `src/petasos/trust/untouched.py`",
    )


def test_main_changed_since_branch_unrelated_path_stays_ready():
    facts = ready_facts(main_changed_since_branch=["docs/a.md"])
    assert evaluate(facts).ready


# ---------------------------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------------------------


def test_ci_missing_not_ready():
    assert_not_ready(ready_facts(ci=None), "no CI run for the head commit")


def test_ci_wrong_path_not_ready():
    ci = replace(ready_facts().ci, path=".github/workflows/other.yml")
    assert_not_ready(ready_facts(ci=ci), "not a pull request run of `ci.yml`")


def test_ci_wrong_event_not_ready():
    ci = replace(ready_facts().ci, event="push")
    assert_not_ready(ready_facts(ci=ci), "not a pull request run of `ci.yml`")


def test_ci_wrong_head_sha_not_ready():
    ci = replace(ready_facts().ci, head_sha=OLD_SHA)
    assert_not_ready(ready_facts(ci=ci), "not a pull request run of `ci.yml`")


@pytest.mark.parametrize("job", ["python", "demo", "private-identifiers"])
@pytest.mark.parametrize("conclusion", ["failure", None], ids=["failure", "missing"])
def test_ci_required_job_failing_or_missing(job, conclusion):
    jobs = {"python": "success", "demo": "success", "private-identifiers": "success"}
    if conclusion is None:
        del jobs[job]
    else:
        jobs[job] = conclusion
    ci = replace(ready_facts().ci, jobs=jobs)
    assert_not_ready(ready_facts(ci=ci), f"CI job `{job}`")


# ---------------------------------------------------------------------------------------------
# Claude review
# ---------------------------------------------------------------------------------------------


def test_claude_review_missing_not_ready():
    assert_not_ready(ready_facts(claude_review=None), "no Claude review run for the head commit")


def test_claude_review_wrong_path_not_ready():
    cr = replace(ready_facts().claude_review, path=".github/workflows/other.yml")
    assert_not_ready(ready_facts(claude_review=cr), "not a trusted run")


def test_claude_review_wrong_sha_not_ready():
    cr = replace(ready_facts().claude_review, head_sha=OLD_SHA)
    assert_not_ready(ready_facts(claude_review=cr), "not a trusted run")


@pytest.mark.parametrize("conclusion", ["skipped", "failure", "cancelled", None])
def test_claude_review_job_not_success(conclusion):
    jobs = {} if conclusion is None else {"review": conclusion}
    cr = replace(ready_facts().claude_review, jobs=jobs)
    assert_not_ready(ready_facts(claude_review=cr), "not a clean pass")


def test_claude_bot_comment_no_longer_counts_regression():
    """Finding 1: PullFacts carries no issue-comment channel for the Claude review at all, so
    a builder-posted `claude[bot]` comment cannot substitute for the workflow evidence, even
    with a Codex review already present on the head commit."""
    assert "issue_comments" not in PullFacts.__dataclass_fields__
    facts = ready_facts(claude_review=None)
    assert facts.reviews  # a Codex review is present
    verdict = evaluate(facts)
    assert not verdict.ready
    assert any("Claude review" in r for r in verdict.reasons)


# ---------------------------------------------------------------------------------------------
# Codex review
# ---------------------------------------------------------------------------------------------


def test_codex_review_missing_not_ready():
    assert_not_ready(ready_facts(reviews=[]), "no Codex review for the head commit")


def test_codex_review_on_old_sha_only_not_ready_regression():
    """Finding 2: a review left on a stale commit must not approve a newer one, and a stale
    thumbs-up flag must not paper over it either."""
    facts = ready_facts(
        reviews=[Review("chatgpt-codex-connector[bot]", "COMMENTED", "Looks fine.", OLD_SHA)],
        codex_thumbs_on_request=False,
    )
    assert_not_ready(facts, "no Codex review for the head commit")


def test_codex_thumbs_on_request_without_review_is_ready():
    facts = ready_facts(reviews=[], codex_thumbs_on_request=True)
    assert evaluate(facts).ready


def test_codex_blocker_body_blocks():
    facts = ready_facts(
        reviews=[
            Review("chatgpt-codex-connector[bot]", "COMMENTED", "blocker | fix the auth check", SHA)
        ]
    )
    assert_not_ready(facts, "a Codex finding on the head commit is a blocker")


def test_codex_p1_tag_blocks():
    facts = ready_facts(
        reviews=[
            Review("chatgpt-codex-connector[bot]", "COMMENTED", "[P1] tighten validation", SHA)
        ]
    )
    assert_not_ready(facts, "blocker (blocker, P0, or P1)")


def test_codex_p0_tag_blocks():
    facts = ready_facts(
        reviews=[Review("chatgpt-codex-connector[bot]", "COMMENTED", "severity P0 here", SHA)]
    )
    assert_not_ready(facts, "blocker (blocker, P0, or P1)")


def test_codex_inline_p1_on_head_sha_blocks():
    facts = ready_facts(
        inline_comments=[Comment("chatgpt-codex-connector[bot]", "P1 inline finding", SHA)]
    )
    assert_not_ready(facts, "blocker (blocker, P0, or P1)")


def test_codex_inline_p1_on_old_sha_does_not_block():
    facts = ready_facts(
        inline_comments=[Comment("chatgpt-codex-connector[bot]", "P1 inline finding", OLD_SHA)]
    )
    assert evaluate(facts).ready


def test_codex_changes_requested_blocks():
    facts = ready_facts(
        reviews=[Review("chatgpt-codex-connector[bot]", "CHANGES_REQUESTED", "please fix", SHA)]
    )
    assert_not_ready(facts, "Codex requested changes")


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------


def test_report_missing_not_ready():
    assert_not_ready(ready_facts(report_text=None), "report.md` is missing at the head commit")


@pytest.mark.parametrize("verdict_line", ["Verdict: BUILT WITH FLAGS", "Verdict: BLOCKED"])
def test_report_wrong_verdict_not_ready(verdict_line):
    assert_not_ready(
        ready_facts(report_text=f"# Report\n\n{verdict_line}\n"),
        "verdict is not exactly",
    )


@pytest.mark.parametrize("placeholder", ["TODO", "TBD", "<!--", "[fill"])
def test_report_placeholder_not_ready(placeholder):
    text = f"# Report\n\nVerdict: BUILT\n\n{placeholder} something\n"
    assert_not_ready(ready_facts(report_text=text), "placeholder")


# ---------------------------------------------------------------------------------------------
# Pull request body
# ---------------------------------------------------------------------------------------------


def test_body_without_wall_section_not_ready():
    assert_not_ready(ready_facts(body="Nothing here.\n"), "## Wall check")


# ---------------------------------------------------------------------------------------------
# Wall: plan presence, wall_expected, files, tasks.md, QUESTIONS.md
# ---------------------------------------------------------------------------------------------


def test_plan_missing_not_ready():
    assert_not_ready(ready_facts(plan_text=None), "plan.md` is missing on `main`")


def test_empty_wall_expected_not_ready():
    plan = "grounded_at: `1234567`\n\nwall_expected:\n\nMore prose.\n"
    assert_not_ready(ready_facts(plan_text=plan), "`wall_expected` list is empty")


def test_file_outside_wall_not_ready():
    facts = ready_facts(files=default_files() + [FileChange("src/petasos/other/thing.py", "added")])
    assert_not_ready(facts, "outside the change's wall")


STANDING_FORBIDDEN_PATHS = [
    "AGENTS.md",
    "DECISIONS.md",
    "uv.lock",
    "pyproject.toml",
    ".github/workflows/ci.yml",
    ".github/scripts/merge_gate.py",
    "changes/001-trust-core/spec.md",
    "changes/001-trust-core/plan.md",
    "changes/002-memory-canary-guard/proposal.md",
    "tests/test_no_private_identifiers.py",
    "tests/test_merge_gate.py",
    "review/x.md",
]


@pytest.mark.parametrize("path", STANDING_FORBIDDEN_PATHS)
def test_standing_forbidden_path_blocked_even_when_in_wall_expected(path):
    plan = PLAN_TEXT.replace(
        "- `src/petasos/trust/**`",
        f"- `src/petasos/trust/**`\n- `{path}`",
    )
    facts = ready_facts(plan_text=plan, files=default_files() + [FileChange(path, "modified")])
    assert_not_ready(facts, "is standing-forbidden")


def test_plan_wall_forbidden_path_blocked_even_when_in_wall_expected():
    plan = (
        "grounded_at: `1234567`\n"
        "\n"
        "wall_expected:\n"
        "- `src/petasos/**`\n"
        "\n"
        "wall_forbidden: do not touch `src/petasos/__init__.py`.\n"
        "\n"
        "More prose.\n"
    )
    facts = ready_facts(
        plan_text=plan,
        files=default_files() + [FileChange("src/petasos/__init__.py", "modified")],
    )
    assert_not_ready(facts, "is in the plan's `wall_forbidden`")


def test_rename_checks_old_path_too_regression():
    """Finding 4: a rename must be checked at both its new and its previous path."""
    facts = ready_facts(
        files=default_files()
        + [
            FileChange(
                "src/petasos/trust/copied.md", "renamed", previous_path="review/trusted-review.md"
            )
        ]
    )
    assert_not_ready(facts, "review/trusted-review.md")


def test_rename_without_previous_path_not_ready():
    facts = ready_facts(
        files=default_files() + [FileChange("src/petasos/trust/copied.md", "renamed")]
    )
    assert_not_ready(facts, "renamed or copied from an unknown path")


def test_removed_file_outside_wall_not_ready():
    facts = ready_facts(
        files=default_files() + [FileChange("src/petasos/other/gone.py", "removed")]
    )
    assert_not_ready(facts, "outside the change's wall")


def test_files_incomplete_not_ready():
    assert_not_ready(ready_facts(files_complete=False), "incomplete")


def test_no_files_not_ready():
    assert_not_ready(ready_facts(files=[]), "changes no files")


def test_tasks_md_tick_only_patch_is_ready():
    assert evaluate(ready_facts()).ready


def _files_without_tasks() -> list[FileChange]:
    return [f for f in default_files() if f.path != TASKS_PATH]


def test_tasks_md_patch_editing_text_blocked():
    files = _files_without_tasks() + [
        FileChange(
            TASKS_PATH, "modified", patch="@@ -1,2 +1,2 @@\n-- [ ] Write x\n+- [x] Write y\n"
        )
    ]
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_tasks_md_unticking_blocked():
    files = _files_without_tasks() + [
        FileChange(
            TASKS_PATH, "modified", patch="@@ -1,2 +1,2 @@\n-- [x] Write x\n+- [ ] Write x\n"
        )
    ]
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_tasks_md_status_added_blocked():
    files = _files_without_tasks() + [
        FileChange(TASKS_PATH, "added", patch="@@ -0,0 +1,2 @@\n+- [x] Write x\n")
    ]
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_tasks_md_patch_none_blocked():
    files = _files_without_tasks() + [FileChange(TASKS_PATH, "modified", patch=None)]
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_questions_md_append_only_patch_is_ready():
    files = default_files() + [
        FileChange("changes/QUESTIONS.md", "modified", patch="@@ -5,0 +6,2 @@\n+## 2026\n+1. q\n")
    ]
    assert evaluate(ready_facts(files=files)).ready


def test_questions_md_removed_line_blocked():
    files = default_files() + [
        FileChange(
            "changes/QUESTIONS.md", "modified", patch="@@ -5,2 +5,1 @@\n-1. old q\n+## 2026\n"
        )
    ]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


def test_questions_md_status_removed_blocked():
    files = default_files() + [FileChange("changes/QUESTIONS.md", "removed", patch=None)]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


def test_questions_md_patch_none_blocked():
    files = default_files() + [FileChange("changes/QUESTIONS.md", "modified", patch=None)]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


# ---------------------------------------------------------------------------------------------
# Helper unit tests: glob_to_regex / matches_any
# ---------------------------------------------------------------------------------------------


def test_glob_double_star_crosses_folders():
    assert matches_any("src/petasos/trust/deep/grants.py", ["src/petasos/trust/**"])


def test_glob_single_star_does_not_cross_folders():
    assert not matches_any("src/petasos/trust/deep/grants.py", ["src/petasos/trust/*"])
    assert matches_any("src/petasos/trust/grants.py", ["src/petasos/trust/*"])


def test_glob_changes_star_slug_matches_one_segment():
    assert matches_any("changes/001-trust-core/spec.md", ["changes/*/spec.md"])
    assert not matches_any("changes/spec.md", ["changes/*/spec.md"])


# ---------------------------------------------------------------------------------------------
# Helper unit tests: parse_wall_expected / parse_wall_forbidden / parse_depends_on
# ---------------------------------------------------------------------------------------------


def test_parse_wall_expected_ignores_annotations_and_stops_at_blank_line():
    plan = (
        "wall_expected:\n"
        "- `src/petasos/trust/**`\n"
        "- `changes/001-trust-core/tasks.md` (ticking boxes only)\n"
        "\n"
        "- `should/not/appear.py`\n"
    )
    assert parse_wall_expected(plan) == [
        "src/petasos/trust/**",
        "changes/001-trust-core/tasks.md",
    ]


def test_parse_wall_forbidden_picks_paths_from_its_paragraph_only():
    plan = (
        "wall_forbidden: do not touch `src/petasos/__init__.py`.\n"
        "\n"
        "Another paragraph mentions `not/from/here.py` too.\n"
    )
    assert parse_wall_forbidden(plan) == ["src/petasos/__init__.py"]


def test_parse_depends_on_none_is_empty_list():
    assert parse_depends_on("depends_on: none\n") == []


def test_parse_depends_on_multiple_changes():
    assert parse_depends_on("depends_on: 000-bootstrap, 002-memory-canary-guard\n") == [
        "000-bootstrap",
        "002-memory-canary-guard",
    ]


def test_parse_depends_on_missing_line_is_none():
    assert parse_depends_on("status: ratified\n") is None


# ---------------------------------------------------------------------------------------------
# Helper unit tests: only_ticks / only_appends
# ---------------------------------------------------------------------------------------------


def test_only_ticks_true_for_tick_only_patch():
    assert only_ticks(TICK_ONLY_PATCH)


def test_only_ticks_false_for_no_patch():
    assert not only_ticks(None)


def test_only_ticks_false_when_removed_and_added_counts_differ():
    assert not only_ticks("@@ -1,1 +1,2 @@\n-- [ ] a\n+- [x] a\n+- [ ] b\n")


def test_only_ticks_false_when_text_also_changes():
    assert not only_ticks("@@ -1,1 +1,1 @@\n-- [ ] a\n+- [x] b\n")


def test_only_appends_true_for_pure_addition():
    assert only_appends("@@ -5,0 +6,2 @@\n+## 2026\n+1. q\n")


def test_only_appends_false_for_no_patch():
    assert not only_appends(None)


def test_only_appends_false_when_anything_removed():
    assert not only_appends("@@ -1,1 +1,1 @@\n-old\n+new\n")


# ---------------------------------------------------------------------------------------------
# Helper unit tests: is_blocker_text
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["blocker | a", "**blocker** | x", "- blocker: x", "BLOCKER | x", "[blocker] | x"],
)
def test_is_blocker_text_true(text):
    assert is_blocker_text(text)


@pytest.mark.parametrize(
    "text",
    ["should-fix | x", "nit | x", "Blockers: 0", "this is not a blocker because", "unblocker | x"],
)
def test_is_blocker_text_false(text):
    assert not is_blocker_text(text)


# ---------------------------------------------------------------------------------------------
# Helper unit tests: should_request_codex
# ---------------------------------------------------------------------------------------------


def test_should_request_codex_true_when_only_codex_missing_and_ci_green():
    facts = ready_facts(reviews=[])
    verdict = evaluate(facts)
    assert should_request_codex(facts, verdict, [])


def test_should_request_codex_false_when_ci_job_failing():
    ci = replace(
        ready_facts().ci,
        jobs={"python": "failure", "demo": "success", "private-identifiers": "success"},
    )
    facts = ready_facts(reviews=[], ci=ci)
    verdict = evaluate(facts)
    assert not should_request_codex(facts, verdict, [])


def test_should_request_codex_false_when_ci_none():
    facts = ready_facts(reviews=[], ci=None)
    verdict = evaluate(facts)
    assert not should_request_codex(facts, verdict, [])


def test_should_request_codex_false_when_already_requested():
    facts = ready_facts(reviews=[])
    verdict = evaluate(facts)
    assert not should_request_codex(facts, verdict, [codex_request_body(facts.head_sha)])


def test_should_request_codex_true_for_a_new_sha():
    facts = ready_facts(reviews=[])
    verdict = evaluate(facts)
    assert should_request_codex(facts, verdict, [codex_request_body(OLD_SHA)])


# ---------------------------------------------------------------------------------------------
# main(): fake GitHub, no network
# ---------------------------------------------------------------------------------------------


def open_pr_dict() -> dict:
    return {
        "number": 1,
        "title": "[build] 001-trust-core",
        "head": {"sha": SHA, "ref": "build/001-trust-core", "repo": {"full_name": "x/y"}},
        "base": {"ref": "main"},
        "labels": [],
        "draft": False,
        "body": "",
    }


class FakeGitHubForMain:
    """Records every call; answers just enough for `main()` to run end to end."""

    def __init__(self, merge_result: dict) -> None:
        self.repo = "x/y"
        self.merge_result = merge_result
        self.calls: list[tuple] = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == "/branches/main":
            return {"commit": {"sha": MAIN_SHA}}
        if method == "PUT" and path.endswith("/merge"):
            return self.merge_result
        if method == "POST" and path.endswith("/comments"):
            return {"id": 99}
        return {}

    def paged(self, path, key=None, limit_pages=30):
        self.calls.append(("PAGED", path, key))
        if path.startswith("/pulls?state=open"):
            return [open_pr_dict()]
        return []


def run_main(fake, facts, dry_run=False):
    env = {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": "x/y"}
    if dry_run:
        env["MERGE_GATE_DRY_RUN"] = "true"
    captured: list[str] = []
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        with (
            patch.object(merge_gate, "GitHub", return_value=fake),
            patch.object(merge_gate, "gather", return_value=facts),
            patch.object(merge_gate, "summary", side_effect=captured.extend),
        ):
            merge_gate.main()
    return captured


def test_main_merge_not_confirmed_runs_no_cleanup_regression():
    """Finding 8: cleanup (branch delete, CI dispatch) must wait for a confirmed merge."""
    fake = FakeGitHubForMain(merge_result={"merged": False, "message": "no"})
    lines = run_main(fake, ready_facts())
    assert not any(c[0] == "DELETE" for c in fake.calls)
    assert not any(c[0] == "POST" and "dispatches" in c[1] for c in fake.calls)
    assert any("did not confirm" in line for line in lines)


def test_main_confirmed_merge_deletes_branch_and_dispatches_ci():
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    run_main(fake, ready_facts())

    deletes = [c for c in fake.calls if c[0] == "DELETE"]
    assert deletes and deletes[0][1] == "/git/refs/heads/build/001-trust-core"

    dispatches = [c for c in fake.calls if c[0] == "POST" and "dispatches" in c[1]]
    assert dispatches

    puts = [c for c in fake.calls if c[0] == "PUT"]
    assert len(puts) == 1
    _, _, put_body = puts[0]
    assert put_body["sha"] == SHA
    assert put_body["merge_method"] == "squash"


def test_main_not_ready_posts_status_comment_with_reason():
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    run_main(fake, ready_facts(draft=True))

    assert not any(c[0] == "PUT" for c in fake.calls)
    posts = [c for c in fake.calls if c[0] == "POST" and c[1] == "/issues/1/comments"]
    assert posts
    body = posts[0][2]["body"]
    assert STATUS_MARKER in body
    assert "draft" in body


def test_main_dry_run_does_not_merge_or_delete():
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    run_main(fake, ready_facts(), dry_run=True)
    assert not any(c[0] == "PUT" for c in fake.calls)
    assert not any(c[0] == "DELETE" for c in fake.calls)


# ---------------------------------------------------------------------------------------------
# gather(): fake GitHub, no network
# ---------------------------------------------------------------------------------------------


class FakeGitHubForGather:
    def __init__(self) -> None:
        self.repo = "x/y"
        self.file_at_calls: list[tuple] = []
        self.compare_calls: list[tuple] = []

    def request(self, method, path, body=None):
        if method == "GET" and path == "/pulls/1":
            return {"changed_files": 5}
        return {}

    def paged(self, path, key=None, limit_pages=30):
        if path == "/pulls/1/files":
            return [
                {"filename": "src/petasos/trust/grants.py", "status": "modified"},
                {
                    "filename": "src/petasos/trust/renamed.py",
                    "previous_filename": "src/petasos/trust/old.py",
                    "status": "renamed",
                },
            ]
        if path in ("/issues/1/comments", "/pulls/1/reviews", "/pulls/1/comments"):
            return []
        return []

    def file_at(self, path, ref):
        self.file_at_calls.append((path, ref))
        if path.endswith("report.md"):
            return REPORT_TEXT
        if path.endswith("plan.md"):
            return PLAN_TEXT
        if path.endswith("proposal.md"):
            return PROPOSAL_TEXT
        return None

    def compare_files(self, base, head):
        self.compare_calls.append((base, head))
        return {"status": "ahead", "merge_base_commit": {"sha": MAIN_SHA}}, []

    def latest_run(self, workflow_path, sha):
        return None


def test_gather_reads_files_renames_and_main_pinned_paths():
    fake = FakeGitHubForGather()
    facts = gather(fake, open_pr_dict(), MAIN_SHA, frozenset())

    renamed = next(f for f in facts.files if f.path == "src/petasos/trust/renamed.py")
    assert renamed.previous_path == "src/petasos/trust/old.py"

    # GET /pulls/1 says 5 changed files but only 2 were listed: the file list is incomplete.
    assert facts.files_complete is False

    assert ("changes/001-trust-core/plan.md", MAIN_SHA) in fake.file_at_calls
    assert ("changes/001-trust-core/proposal.md", MAIN_SHA) in fake.file_at_calls
    assert ("changes/001-trust-core/report.md", SHA) in fake.file_at_calls
