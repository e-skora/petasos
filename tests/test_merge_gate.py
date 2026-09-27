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
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import ClassVar
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
QUESTIONS_PATH = merge_gate.QUESTIONS_PATH
TASKS_BEFORE = "# Tasks\n\n- [ ] Write x\n- [ ] Write y\n"
TASKS_AFTER = "# Tasks\n\n- [x] Write x\n- [ ] Write y\n"
QUESTIONS_BEFORE = "# Questions\n\n1. old q\n"


def tasks_change(before=TASKS_BEFORE, after=TASKS_AFTER, status="modified") -> FileChange:
    return FileChange(TASKS_PATH, status, before=before, after=after)


def questions_change(after, before=QUESTIONS_BEFORE, status="modified") -> FileChange:
    return FileChange(QUESTIONS_PATH, status, before=before, after=after)


def default_files() -> list[FileChange]:
    return [
        FileChange("src/petasos/trust/grants.py", "modified"),
        FileChange("tests/test_trust_grants.py", "added"),
        FileChange("changes/001-trust-core/report.md", "added"),
        tasks_change(),
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
        "behind_main": False,
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


def test_behind_main_unknown_not_ready():
    assert_not_ready(
        ready_facts(behind_main=None),
        "could not tell whether the branch contains the current `main`",
    )


def test_behind_main_not_ready_regression():
    """Third-pass finding 2: any commit on main after the branch was cut makes the pull
    request wait, even when that commit touched nothing in the wall or the file list. The
    old rule let main change pyproject.toml, uv.lock, ci.yml, or the spec without
    invalidating the evidence produced against the older base."""
    assert_not_ready(ready_facts(behind_main=True), merge_gate.BEHIND_MAIN_REASON)
    # The selective rule is gone: there is no per-file list to be incomplete.
    assert "main_changed_since_branch" not in PullFacts.__dataclass_fields__


def test_behind_main_reason_is_the_text_the_builder_prompt_matches():
    workflow = (MODULE_PATH.parents[1] / "workflows/claude-builder.yml").read_text()
    assert merge_gate.BEHIND_MAIN_REASON in workflow


def test_up_to_date_branch_stays_ready():
    assert evaluate(ready_facts(behind_main=False)).ready


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
    assert_not_ready(ready_facts(claude_review=cr), "the Claude review of the head commit")


MODEL_STEP = merge_gate.REVIEW_MODEL_STEP
CHECK_STEP = merge_gate.REVIEW_CHECK_STEP
BLOCKERS_STEP = merge_gate.REVIEW_BLOCKERS_STEP
RUN_URL = "https://github.com/x/y/actions/runs/77"


def test_claude_review_completed_with_blocker_says_so_and_links_the_run():
    """Third-pass finding 3: a completed review that found a blocker is a repair case; the
    builder is told where the findings are. Only the full chain counts: model passed, check
    passed (valid, completed, right commit), blockers step failed."""
    cr = replace(
        ready_facts().claude_review,
        jobs={"review": "failure"},
        url=RUN_URL,
        steps={"review": {MODEL_STEP: "success", CHECK_STEP: "success", BLOCKERS_STEP: "failure"}},
    )
    verdict = evaluate(ready_facts(claude_review=cr))
    assert not verdict.ready
    (reason,) = [r for r in verdict.reasons if "Claude review" in r]
    assert "completed and found a problem" in reason
    assert BLOCKERS_STEP in reason
    assert RUN_URL in reason
    assert "did not complete" not in reason


@pytest.mark.parametrize(
    "steps",
    [
        {MODEL_STEP: "failure", CHECK_STEP: "failure"},  # authentication or a crash
        {MODEL_STEP: "cancelled", CHECK_STEP: "failure"},  # timeout
        {MODEL_STEP: "", CHECK_STEP: ""},  # still running
        {},  # no step detail at all
        # Fourth-pass finding 1: the model step passed but its result was unusable
        # (completed=false, wrong commit, missing output, count mismatch): the check step
        # fails and the blockers step never runs. Not a finding, not a repair case.
        {MODEL_STEP: "success", CHECK_STEP: "failure", BLOCKERS_STEP: "skipped"},
        {MODEL_STEP: "success", CHECK_STEP: "failure"},
        {MODEL_STEP: "success", CHECK_STEP: "skipped", BLOCKERS_STEP: "skipped"},
        {MODEL_STEP: "success", CHECK_STEP: "success", BLOCKERS_STEP: "skipped"},
        {MODEL_STEP: "success", CHECK_STEP: "success", BLOCKERS_STEP: "cancelled"},
        {MODEL_STEP: "success", CHECK_STEP: "success"},
    ],
)
def test_claude_review_not_completed_is_not_a_finding(steps):
    cr = replace(
        ready_facts().claude_review,
        jobs={"review": "failure"},
        url=RUN_URL,
        steps={"review": steps},
    )
    verdict = evaluate(ready_facts(claude_review=cr))
    assert not verdict.ready
    (reason,) = [r for r in verdict.reasons if "Claude review" in r]
    assert "did not complete" in reason
    assert "found a problem" not in reason
    assert RUN_URL in reason


def test_claude_review_failure_without_url_still_explains():
    cr = replace(ready_facts().claude_review, jobs={"review": "failure"})
    reason = merge_gate.claude_review_failure_reason(cr)
    assert "did not complete" in reason
    assert "(run " not in reason


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


def test_tasks_md_tick_only_is_ready():
    assert evaluate(ready_facts()).ready


def _files_without_tasks() -> list[FileChange]:
    return [f for f in default_files() if f.path != TASKS_PATH]


def _with_tasks(**kwargs) -> list[FileChange]:
    return _files_without_tasks() + [tasks_change(**kwargs)]


def test_tasks_md_editing_text_blocked():
    files = _with_tasks(after=TASKS_AFTER.replace("Write y", "Write z"))
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_tasks_md_unticking_blocked():
    files = _with_tasks(before=TASKS_AFTER, after=TASKS_BEFORE)
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_tasks_md_status_added_blocked():
    files = _with_tasks(before=None, after=TASKS_AFTER, status="added")
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


@pytest.mark.parametrize("before, after", [(None, TASKS_AFTER), (TASKS_BEFORE, None), (None, None)])
def test_tasks_md_unreadable_side_blocked(before, after):
    files = _with_tasks(before=before, after=after)
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_tasks_md_added_instruction_after_tick_blocked_regression():
    """Third-pass finding 1, first reproduction: ticking a box and adding a line that starts
    with `++` slipped past the diff parser (it read `+++ ...` as a file heading). Whole-file
    comparison sees the extra line."""
    files = _with_tasks(
        before="- [ ] Write x\n", after="- [x] Write x\n++ New unapproved instruction\n"
    )
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


@pytest.mark.parametrize(
    "after",
    [
        TASKS_AFTER + "- [ ] Write z\n",  # a new task
        TASKS_AFTER[:-1],  # trailing newline dropped
        TASKS_AFTER.replace("- [x] Write x\n", "- [x] Write x\n\n"),  # a blank line
        "# Tasks\n\n- [ ] Write y\n- [x] Write x\n",  # reordered
        TASKS_AFTER.replace("- [x] Write x", "- [x] Write x - [ ]"),  # text after the box
    ],
)
def test_tasks_md_any_other_difference_blocked(after):
    files = _with_tasks(after=after)
    assert_not_ready(ready_facts(files=files), "changes more than ticked boxes")


def test_tasks_md_identical_contents_pass_the_shape_check():
    files = _with_tasks(after=TASKS_BEFORE)
    assert evaluate(ready_facts(files=files)).ready


def test_questions_md_append_only_is_ready():
    files = default_files() + [questions_change(QUESTIONS_BEFORE + "\n## 2026\n2. new q\n")]
    assert evaluate(ready_facts(files=files)).ready


def test_questions_md_rewritten_line_blocked():
    files = default_files() + [questions_change("# Questions\n\n1. replacement\n")]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


def test_questions_md_removed_line_blocked():
    files = default_files() + [questions_change("# Questions\n\n## 2026\n")]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


def test_questions_md_deleted_question_hidden_as_diff_header_blocked_regression():
    """Third-pass finding 1, second reproduction: removing an existing line that starts with
    `--` while appending a new one slipped past the diff parser (it read `--- ...` as a file
    heading). Whole-file comparison sees the old text is gone."""
    files = default_files() + [
        questions_change(
            before="-- Existing question\nKeep this\n", after="Keep this\nNew question\n"
        )
    ]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


def test_questions_md_unchanged_contents_blocked():
    files = default_files() + [questions_change(QUESTIONS_BEFORE)]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


def test_questions_md_status_removed_blocked():
    files = default_files() + [FileChange(QUESTIONS_PATH, "removed")]
    assert_not_ready(ready_facts(files=files), "may only gain lines")


@pytest.mark.parametrize("before, after", [(None, "x\n"), (QUESTIONS_BEFORE, None), (None, None)])
def test_questions_md_unreadable_side_blocked(before, after):
    files = default_files() + [questions_change(before=before, after=after)]
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


def test_only_ticks_true_for_ticks_only():
    assert only_ticks("- [ ] a\n- [ ] b\n", "- [x] a\n- [x] b\n")
    assert only_ticks("- [ ] a\n- [ ] b\n", "- [ ] a\n- [x] b\n")


def test_only_ticks_true_for_identical_contents():
    assert only_ticks("- [ ] a\n", "- [ ] a\n")


def test_only_ticks_false_for_a_missing_side():
    assert not only_ticks(None, "- [x] a\n")
    assert not only_ticks("- [ ] a\n", None)


def test_only_ticks_false_when_a_line_is_added_even_with_a_diff_header_prefix():
    assert not only_ticks("- [ ] a\n", "- [x] a\n- [ ] b\n")
    assert not only_ticks("- [ ] a\n", "- [x] a\n++ b\n")
    assert not only_ticks("- [ ] a\n", "- [x] a\n--- b\n")


def test_only_ticks_false_when_a_line_is_removed():
    assert not only_ticks("- [ ] a\n-- b\n", "- [x] a\n")


def test_only_ticks_false_when_text_also_changes():
    assert not only_ticks("- [ ] a\n", "- [x] b\n")
    assert not only_ticks("- [ ] a\n", "- [X] a\n")


def test_only_ticks_false_when_a_box_is_unticked():
    assert not only_ticks("- [x] a\n", "- [ ] a\n")


def test_only_ticks_only_the_first_box_on_a_line_may_change():
    assert only_ticks("- [ ] a - [ ] b\n", "- [x] a - [ ] b\n")
    assert not only_ticks("- [ ] a - [ ] b\n", "- [x] a - [x] b\n")


def test_only_ticks_false_when_trailing_newline_changes():
    assert not only_ticks("- [ ] a\n", "- [x] a")
    assert not only_ticks("- [ ] a", "- [x] a\n")


def test_only_appends_true_for_whole_new_lines_at_the_end():
    assert only_appends("a\n", "a\nb\n")
    assert only_appends("a\n", "a\n\n## 2026\n1. q\n")
    assert only_appends("a", "a\nb\n")  # old last line kept whole
    assert only_appends("", "b\n")


def test_only_appends_false_for_a_missing_side():
    assert not only_appends(None, "a\nb\n")
    assert not only_appends("a\n", None)


def test_only_appends_false_when_nothing_is_added():
    assert not only_appends("a\n", "a\n")
    assert not only_appends("a\n", "a\n\n")


def test_only_appends_false_when_old_text_changes_or_moves():
    assert not only_appends("a\nb\n", "a\nc\n")  # rewritten
    assert not only_appends("a\nb\n", "a\n")  # removed
    assert not only_appends("a\nb\n", "b\na\n")  # reordered
    assert not only_appends("a\nb\n", "c\na\nb\n")  # prepended
    assert not only_appends("a", "ab\n")  # old last line extended
    assert not only_appends("-- old\nkeep\n", "keep\nnew\n")  # finding 1 reproduction


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


class FakeTime:
    """A clock that only moves when the gate sleeps: no real waiting in tests."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


def run_main(fake, facts, dry_run=False, wait_seconds="0", gather_side_effect=None):
    """Run main() against a fake GitHub. `facts` is what gather() returns; a list gives one
    return per call (the last repeats). The Codex wait is off unless wait_seconds says
    otherwise, and time is faked either way."""
    env = {
        "GITHUB_TOKEN": "token",
        "GITHUB_REPOSITORY": "x/y",
        "MERGE_GATE_CODEX_WAIT_SECONDS": wait_seconds,
        "MERGE_GATE_CODEX_POLL_SECONDS": "30",
    }
    if dry_run:
        env["MERGE_GATE_DRY_RUN"] = "true"
    captured: list[str] = []
    ft = FakeTime()
    sequence = list(facts) if isinstance(facts, list) else [facts]

    def fake_gather(*args, **kwargs):
        return sequence.pop(0) if len(sequence) > 1 else sequence[0]

    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        with (
            patch.object(merge_gate, "GitHub", return_value=fake),
            patch.object(merge_gate, "gather", side_effect=gather_side_effect or fake_gather),
            patch.object(merge_gate, "summary", side_effect=captured.extend),
            patch.object(merge_gate, "_sleep", ft.sleep),
            patch.object(merge_gate, "_clock", ft.clock),
        ):
            merge_gate.main()
    run_main.last_time = ft
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
# Waiting for Codex inside the run (no event fires when a bot posts a review)
# ---------------------------------------------------------------------------------------------


def codex_request_posted(fake) -> bool:
    return any(
        c[0] == "POST"
        and c[1] == "/issues/1/comments"
        and (c[2] or {}).get("body") == codex_request_body(SHA)
        for c in fake.calls
    )


def test_main_waits_for_codex_and_merges_in_the_same_run():
    """The gate asks Codex, keeps polling, sees the review land, re-evaluates, and merges."""
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    lines = run_main(
        fake,
        [ready_facts(reviews=[]), ready_facts(reviews=[]), ready_facts()],
        wait_seconds="600",
    )
    assert codex_request_posted(fake)
    assert run_main.last_time.sleeps == [30, 30]  # two polls, then Codex was there
    assert any(c[0] == "PUT" for c in fake.calls)
    assert any("Codex answered" in line for line in lines)
    assert any("merged as" in line for line in lines)


def test_main_gives_up_waiting_at_the_budget_and_updates_the_status():
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    lines = run_main(fake, ready_facts(reviews=[]), wait_seconds="90")
    assert codex_request_posted(fake)
    assert run_main.last_time.sleeps == [30, 30, 30]
    assert not any(c[0] == "PUT" for c in fake.calls)
    assert any("no Codex answer within 90 seconds" in line for line in lines)
    assert any("no Codex review for the head commit" in line for line in lines)
    # The status comment was written twice: before the wait and after it.
    statuses = [
        c
        for c in fake.calls
        if c[0] == "POST"
        and c[1] == "/issues/1/comments"
        and STATUS_MARKER in (c[2] or {}).get("body", "")
    ]
    assert len(statuses) >= 1


def test_main_does_not_wait_when_the_budget_is_zero():
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    run_main(fake, ready_facts(reviews=[]), wait_seconds="0")
    assert codex_request_posted(fake)
    assert run_main.last_time.sleeps == []
    assert not any(c[0] == "PUT" for c in fake.calls)


def test_main_does_not_wait_when_codex_was_not_asked():
    """No request means no wait: for example CI is red, so Codex is not asked yet."""
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    ci = replace(ready_facts().ci, jobs={"python": "failure", "demo": "success"})
    run_main(fake, ready_facts(reviews=[], ci=ci), wait_seconds="600")
    assert not codex_request_posted(fake)
    assert run_main.last_time.sleeps == []


def test_main_wait_never_merges_when_codex_answers_with_a_blocker():
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    blocked = ready_facts(
        reviews=[Review("chatgpt-codex-connector[bot]", "COMMENTED", "P1 bad", SHA)]
    )
    lines = run_main(fake, [ready_facts(reviews=[]), blocked], wait_seconds="600")
    assert run_main.last_time.sleeps == [30]
    assert not any(c[0] == "PUT" for c in fake.calls)
    assert any("is a blocker" in line for line in lines)


def test_wait_for_codex_stops_at_thumbs_up_on_the_request():
    ft = FakeTime()
    seq = [ready_facts(reviews=[]), ready_facts(reviews=[], codex_thumbs_on_request=True)]
    with patch.object(merge_gate, "gather", side_effect=lambda *a, **k: seq.pop(0)):
        facts = merge_gate.wait_for_codex(
            None, {}, MAIN_SHA, frozenset(), ready_facts(reviews=[]), 600, 30, ft.sleep, ft.clock
        )
    assert facts.codex_thumbs_on_request
    assert ft.sleeps == [30, 30]


def test_wait_for_codex_failure_inside_the_wait_leaves_the_pr_waiting():
    fake = FakeGitHubForMain(merge_result={"merged": True, "sha": MERGE_SHA})
    seq = [ready_facts(reviews=[])]

    def gather_then_raise(*a, **k):
        if seq:
            return seq.pop(0)
        raise OSError("network down")

    lines = run_main(
        fake, ready_facts(reviews=[]), wait_seconds="600", gather_side_effect=gather_then_raise
    )
    assert not any(c[0] == "PUT" for c in fake.calls)
    assert any("the wait failed" in line for line in lines)


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
    # Contents are fetched only for the shape-checked paths, and none was in this list.
    assert not any(path.endswith("grants.py") for path, _ in fake.file_at_calls)
    assert facts.behind_main is False


class FakeGitHubForGatherShapes(FakeGitHubForGather):
    """Lists tasks.md and QUESTIONS.md and serves different contents per ref."""

    contents: ClassVar[dict[tuple[str, str], str]] = {
        (TASKS_PATH, MAIN_SHA): TASKS_BEFORE,
        (TASKS_PATH, SHA): TASKS_AFTER,
        (QUESTIONS_PATH, MAIN_SHA): QUESTIONS_BEFORE,
        (QUESTIONS_PATH, SHA): QUESTIONS_BEFORE + "2. new q\n",
    }

    def paged(self, path, key=None, limit_pages=30):
        if path == "/pulls/1/files":
            return [
                {"filename": TASKS_PATH, "status": "modified"},
                {"filename": QUESTIONS_PATH, "status": "modified"},
            ]
        return []

    def file_at(self, path, ref):
        self.file_at_calls.append((path, ref))
        if (path, ref) in self.contents:
            return self.contents[(path, ref)]
        return super().file_at(path, ref)


def test_gather_fetches_whole_contents_of_shape_checked_files_from_main_and_head():
    fake = FakeGitHubForGatherShapes()
    facts = gather(fake, open_pr_dict(), MAIN_SHA, frozenset())
    by_path = {f.path: f for f in facts.files}
    assert by_path[TASKS_PATH].before == TASKS_BEFORE
    assert by_path[TASKS_PATH].after == TASKS_AFTER
    assert by_path[QUESTIONS_PATH].before == QUESTIONS_BEFORE
    assert by_path[QUESTIONS_PATH].after.endswith("2. new q\n")
    assert (TASKS_PATH, MAIN_SHA) in fake.file_at_calls
    assert (TASKS_PATH, SHA) in fake.file_at_calls
    # No diff text is carried at all: the shape checks cannot fall back to one.
    assert "patch" not in FileChange.__dataclass_fields__


class FakeGitHubForGatherBase(FakeGitHubForGather):
    def __init__(self, merge_base) -> None:
        super().__init__()
        self.merge_base = merge_base

    def compare_files(self, base, head):
        self.compare_calls.append((base, head))
        if (base, head) == (MAIN_SHA, SHA):
            if self.merge_base == "404":
                return None, None
            return {"status": "diverged", "merge_base_commit": {"sha": self.merge_base}}, []
        return {"status": "ahead", "merge_base_commit": {"sha": MAIN_SHA}}, []


def test_gather_marks_a_branch_behind_main_when_the_merge_base_is_older():
    facts = gather(FakeGitHubForGatherBase(OLD_SHA), open_pr_dict(), MAIN_SHA, frozenset())
    assert facts.behind_main is True
    assert merge_gate.BEHIND_MAIN_REASON in evaluate(facts).reasons


def test_gather_marks_a_branch_current_when_the_merge_base_is_main():
    facts = gather(FakeGitHubForGatherBase(MAIN_SHA), open_pr_dict(), MAIN_SHA, frozenset())
    assert facts.behind_main is False


def test_gather_leaves_behind_main_unknown_when_the_compare_fails():
    facts = gather(FakeGitHubForGatherBase("404"), open_pr_dict(), MAIN_SHA, frozenset())
    assert facts.behind_main is None
    assert not evaluate(facts).ready


class FakeGitHubForRuns(merge_gate.GitHub):
    """Answers the two Actions endpoints `latest_run` reads, with step detail."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, method, path, body=None):
        self.calls.append(path)
        if path.startswith("/actions/workflows/claude-review.yml/runs"):
            return {
                "workflow_runs": [
                    {
                        "id": 11,
                        "event": "pull_request",
                        "path": merge_gate.REVIEW_WORKFLOW,
                        "head_sha": SHA,
                        "status": "completed",
                        "html_url": RUN_URL,
                    }
                ]
            }
        assert path == "/actions/runs/11/jobs?per_page=100"
        return {
            "jobs": [
                {
                    "name": "review",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "Set up job", "conclusion": "success"},
                        {"name": MODEL_STEP, "conclusion": "success"},
                        {"name": CHECK_STEP, "conclusion": "success"},
                        {"name": BLOCKERS_STEP, "conclusion": "failure"},
                    ],
                }
            ]
        }


def test_latest_run_carries_the_run_url_and_step_conclusions():
    evidence = FakeGitHubForRuns().latest_run(merge_gate.REVIEW_WORKFLOW, SHA)
    assert evidence.url == RUN_URL
    assert evidence.jobs == {"review": "failure"}
    assert evidence.steps["review"][MODEL_STEP] == "success"
    assert evidence.steps["review"][CHECK_STEP] == "success"
    assert evidence.steps["review"][BLOCKERS_STEP] == "failure"
    reason = merge_gate.claude_review_failure_reason(evidence)
    assert "completed and found a problem" in reason and RUN_URL in reason


# ---------------------------------------------------------------------------------------------
# The review workflow's own script steps, executed with synthetic model output (no model,
# no workflow run). Pins the step names the gate reads and the split the gate relies on.
# ---------------------------------------------------------------------------------------------

REVIEW_WORKFLOW_PATH = MODULE_PATH.parents[1] / "workflows/claude-review.yml"


def review_step_scripts() -> dict[str, str]:
    """Each `name:` step's embedded `python3 - <<'PY'` body, de-indented, keyed by step name."""
    text = REVIEW_WORKFLOW_PATH.read_text()
    scripts: dict[str, str] = {}
    for block in text.split("      - name: ")[1:]:
        name = block.split("\n", 1)[0].strip()
        if "python3 - <<'PY'\n" not in block:
            continue
        body = block.split("python3 - <<'PY'\n", 1)[1].split("          PY", 1)[0]
        scripts[name] = "\n".join(line[10:] for line in body.splitlines())
    return scripts


def run_review_step(name: str, raw: str | None, head_sha: str) -> tuple[int, str]:
    """Run one script step with RESULT=raw (unset when None) and HEAD_SHA=head_sha."""
    env = {"HEAD_SHA": head_sha}
    if raw is not None:
        env["RESULT"] = raw
    proc = subprocess.run(
        [sys.executable, "-c", review_step_scripts()[name]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def clean_result(**overrides) -> str:
    result = {"commit": SHA, "completed": True, "blockers": 0, "findings": []}
    result.update(overrides)
    return json.dumps(result)


BLOCKER_FINDING = {"severity": "blocker", "location": "sample.py:1", "summary": "Problem"}


def test_review_workflow_has_the_step_names_the_gate_reads():
    text = REVIEW_WORKFLOW_PATH.read_text()
    for name in (MODEL_STEP, CHECK_STEP, BLOCKERS_STEP):
        assert f"      - name: {name}\n" in text
    scripts = review_step_scripts()
    assert set(scripts) == {CHECK_STEP, BLOCKERS_STEP}
    # The check step runs even after a failed model step; the blockers step does not.
    check_block = text.split(f"      - name: {CHECK_STEP}\n", 1)[1].split("      - name: ", 1)[0]
    assert "if: always()" in check_block
    blockers_block = text.split(f"      - name: {BLOCKERS_STEP}\n", 1)[1]
    assert "if:" not in blockers_block.split("run:", 1)[0]


def test_review_check_step_passes_a_valid_completed_result_even_with_blockers():
    code, out = run_review_step(CHECK_STEP, clean_result(), SHA)
    assert code == 0, out
    code, out = run_review_step(
        CHECK_STEP, clean_result(blockers=1, findings=[BLOCKER_FINDING]), SHA
    )
    assert code == 0, out


@pytest.mark.parametrize(
    "raw",
    [
        None,  # no structured output at all
        "{not json",
        clean_result(completed=False),  # fourth-pass finding 1
        clean_result(commit=OLD_SHA),  # wrong commit
        clean_result(blockers=0, findings=[BLOCKER_FINDING]),  # count mismatch
        clean_result(blockers=2, findings=[BLOCKER_FINDING]),
    ],
)
def test_review_check_step_fails_an_invalid_or_incomplete_result(raw):
    code, out = run_review_step(CHECK_STEP, raw, SHA)
    assert code != 0
    assert "blocker(s) found" not in out  # never reported as a finding


def test_review_blockers_step_fails_only_when_a_blocker_exists():
    code, out = run_review_step(BLOCKERS_STEP, clean_result(), SHA)
    assert code == 0, out
    should_fix = {"severity": "should-fix", "location": "a.py:1", "summary": "Minor"}
    code, out = run_review_step(BLOCKERS_STEP, clean_result(findings=[should_fix]), SHA)
    assert code == 0, out
    code, out = run_review_step(
        BLOCKERS_STEP, clean_result(blockers=1, findings=[BLOCKER_FINDING]), SHA
    )
    assert code != 0
    assert "blocker | sample.py:1 | Problem" in out
    assert "1 blocker(s) found" in out
