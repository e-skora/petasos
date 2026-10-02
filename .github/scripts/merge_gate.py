"""The merge gate: squash-merges a builder pull request when a script can prove it is ready.

DECISIONS.md D-015. No model runs here. The scheduled workflow `.github/workflows/merge-gate.yml`
calls `main()`. Everything that decides is in `evaluate()`, a pure function over facts fetched
from the GitHub REST API, so `tests/test_merge_gate.py` can test every rule without a network.

Version 2 (2026-09-26) answers the second-pass review (`review/2026-09-26-second-pass.md`,
findings 1, 2, 4, 5, 8, 11). Evidence now comes only from channels the builder cannot write:
workflow runs of trusted workflow files, Codex's own review objects, and files on `main`.
Version 2.1 (2026-09-26) answers the third-pass review (`review/2026-09-26-third-pass.md`):
finding 1 (the tick-only and append-only checks now compare whole file contents, never a
diff), finding 2 (the head commit must contain the current `main`, so every piece of evidence
was produced against the files it will merge into), and finding 3 (the status comment links
the review run and says whether the Claude review found a problem or did not complete).
Version 3 (2026-10-01) answers the process review (`review/2026-10-01-process-roadblocks.md`,
findings 1, 4, 5, 6 and the missed states):
- Finding 1: every fact about a pull request comes from one fresh `GET /pulls/{number}` read,
  re-read after the other reads, and the gate starts over if the head or base moved; the merge
  re-reads the pull request once more and skips if it is no longer open, is a draft, has the
  `hold` label, or has a new head.
- Finding 4: `wall_problems()` rejects a plan whose `wall_expected` or `wall_forbidden` could
  be misread (wrapped lines, a blank line after the `wall_forbidden:` header, duplicates).
- Finding 5: a CI or Claude review run that is still in progress says "still running" instead of
  failing, and a pull request that is not open waits.
- Finding 6: only Codex reviews in state COMMENTED, APPROVED or CHANGES_REQUESTED count; the
  gate asks Codex only for a pull request that is otherwise eligible, and only the gate's own
  request comments count as an earlier request.
- Missed states: the status comment is updated after a merge or a refused merge; all candidates
  are evaluated and one is merged before any Codex waiting; the Codex wait has one deadline for
  the whole run; `main()` returns 1 when the gate itself failed to do something (a fault) and
  0 when every pull request merged or is plainly waiting.

Version 3.1 (2026-10-02) answers the phase 1 review (`review/2026-10-02-loop-phase1.md`, findings 1 to 7):
the pull request's base commit must equal the `main` commit this run pinned; the gate
re-reads and re-judges the pull request right before it merges; `wall_forbidden` has one
parser with a complete grammar; dismissed Codex reviews still contribute blockers (they are
only refused as proof that Codex finished); Codex is asked only when its missing review is
the sole reason a pull request waits; and a head that keeps moving during the Codex wait is
ordinary waiting, not a fault.

A builder pull request merges only when ALL of these hold on its current head commit H:

1. Shape: title `[build] NNN-slug`, head branch `build/NNN-slug` in this repository, base
   `main`, not a draft, no `hold` label.
2. Authorized: on `main`, the change's proposal says `status: ratified`; every change in its
   `depends_on` has a merged `[build]` pull request; its plan names a `grounded_at` commit that
   `main` contains; and `main` has not changed any file in the change's wall since then.
3. Current: H contains the `main` commit this run pinned (the merge base of H and `main` is
   `main` itself). Any commit on `main` after the branch was cut, whatever file it touched,
   makes the pull request wait until the builder merges `main` into the branch; that push
   produces a new H, so CI and both reviews run again against the current dependencies,
   workflows, and specs. The `main` ruleset also requires an up-to-date branch, so GitHub
   refuses the merge itself if `main` moves between this check and the merge call.
4. CI: the latest `CI` workflow run (`.github/workflows/ci.yml`, event `pull_request`) for H
   has jobs `python`, `demo`, and `private-identifiers`, each `success`.
5. Claude review: the latest `Claude review` workflow run for H has its `review` job at
   `success`. That job fails unless the model returned a completed review of H with zero
   blockers (it checks the model's structured result in a separate script step), so a
   comment, a skipped run, or a failed run never counts. When it fails, the status comment
   links the run and says "completed and found a problem" only when the model step and the
   check step passed and the blockers step failed (a valid, completed review of H with a
   blocker); any other shape is "did not complete or was invalid", which the builder never
   repairs (fourth-pass review, finding 1).
6. Codex review: Codex posted a pull request review whose commit is H, or reacted with a
   thumbs-up to the gate's own request comment naming H. None of its findings on H is a
   blocker (`blocker`, `P0`, `P1`), and it did not request changes on H.
7. Report: `changes/NNN-slug/report.md` at H says exactly `Verdict: BUILT`, no placeholders.
8. Wall: the file list is complete; every changed path, and the old path of every rename,
   is inside `wall_expected`, outside the plan's `wall_forbidden`, and not standing-forbidden.
   `tasks.md` and `changes/QUESTIONS.md` are compared as whole files, the copy on `main`
   against the copy at H: `tasks.md` may differ only by `- [ ]` becoming `- [x]` on otherwise
   identical lines; `changes/QUESTIONS.md` at H must start with the exact text of the copy on
   `main` and only add whole lines after it.
9. The pull request body has a `## Wall check` section.

Anything else waits. The reasons go into one status comment on the pull request, which the
builder reads when it repairs. The merge sends H's sha, so a push between check and merge
makes GitHub refuse it, and cleanup runs only after GitHub says the merge happened.

When the gate runs: after every completed `CI` or `Claude review` workflow run (the
`workflow_run` event, which always runs the copy of this gate on `main`), on a schedule as
a backstop (GitHub delays and drops schedule events; on 2026-09-27 the 15-minute cron fired
once in eight hours), and by hand. When the gate asks Codex for a review, it stays in the
same run and polls for Codex's answer for up to MERGE_GATE_CODEX_WAIT_SECONDS (default 600),
because nothing on GitHub starts a run when a bot posts a review.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

REQUIRED_CI_JOBS = ("python", "demo", "private-identifiers")
CI_WORKFLOW = ".github/workflows/ci.yml"
REVIEW_WORKFLOW = ".github/workflows/claude-review.yml"
REVIEW_JOB = "review"
# Step names inside the review job, as written in claude-review.yml. The model step runs the
# review; the check step fails when the structured result is missing, incomplete, or
# inconsistent; the blockers step runs after a passing check and fails when a blocker exists.
REVIEW_MODEL_STEP = "Review the head commit"
REVIEW_CHECK_STEP = "Check the review result (no model)"
REVIEW_BLOCKERS_STEP = "Require zero blockers (no model)"
CODEX_LOGINS = frozenset({"chatgpt-codex-connector[bot]"})
GATE_LOGIN = "github-actions[bot]"
HOLD_LABEL = "hold"
STATUS_MARKER = "<!-- petasos-merge-gate -->"
# The builder's repair prompt (claude-builder.yml) matches this text; change both together.
BEHIND_MAIN_REASON = "the branch is behind `main`; merge `main` into it"
MAX_FILES = 3000
MAX_COMPARE_FILES = 300
# A Codex review counts only in these states; a dismissed or pending review is not evidence.
CODEX_REVIEW_STATES = frozenset({"COMMENTED", "APPROVED", "CHANGES_REQUESTED"})
NOT_COMPLETED_CONCLUSIONS = frozenset({"cancelled", "timed_out", "failure", "skipped"})
MAX_SNAPSHOT_ATTEMPTS = 3
KEPT_CHANGING_REASON = "the pull request kept changing while the gate read it"
NO_CODEX_REASON = "no Codex review for the head commit"
BASE_MOVED_REASON = (
    "the pull request's base is not the `main` commit this run pinned; it waits for a fresh scan"
)
# The one accepted empty form of a plan's forbidden list: `wall_forbidden: none`.
WALL_FORBIDDEN_EMPTY = "none"

TITLE_RE = re.compile(r"^\[build\] (?P<change>\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*)$")
CHANGE_RE = re.compile(r"\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*")
VERDICT_RE = re.compile(r"(?m)^Verdict:\s*BUILT\s*$")
PLACEHOLDER_RE = re.compile(r"<!--|TODO|TBD|\[fill")
WALL_SECTION_RE = re.compile(r"(?mi)^##\s*Wall check\s*$")
RATIFIED_RE = re.compile(r"(?m)^status:\s*ratified\s*$")
DEPENDS_RE = re.compile(r"(?m)^depends_on:\s*(?P<deps>.*)$")
GROUNDED_RE = re.compile(r"(?m)^grounded_at:\s*`?(?P<sha>[0-9a-f]{7,40})`?")
# A finding is a blocker when its severity field says so. The AGENTS.md shape starts the line
# with the severity; markdown decoration (bold, backticks, brackets, list bullets) is allowed.
BLOCKER_LINE_RE = re.compile(r"(?im)^[\s>*_`\[\-]*blocker[\s*_`\]]*(\||:|-|$)")
CODEX_BADGE_RE = re.compile(r"\bP[01]\b")

# Paths no automated change may touch, whatever its plan says (AGENTS.md section 1).
STANDING_FORBIDDEN = (
    "AGENTS.md",
    "CLAUDE.md",
    "DECISIONS.md",
    "PRODUCT.md",
    "ARCHITECTURE.md",
    "DESIGN.md",
    "uv.lock",
    "pyproject.toml",
    "changes/*/proposal.md",
    "changes/*/spec.md",
    "changes/*/plan.md",
    ".github/**",
    "tests/test_no_private_identifiers.py",
    "tests/test_merge_gate.py",
    "review/**",
)
QUESTIONS_PATH = "changes/QUESTIONS.md"


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str  # added, removed, modified, renamed, copied, changed, unchanged
    previous_path: str | None = None
    # Whole contents on `main` and at the head commit; fetched only for the two paths whose
    # edits are shape-checked (`tasks.md`, `changes/QUESTIONS.md`). None: missing or unread.
    before: str | None = None
    after: str | None = None


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    commit_id: str | None = None  # inline review comments carry the commit they were made on


@dataclass(frozen=True)
class Review:
    author: str
    state: str
    body: str
    commit_id: str


@dataclass(frozen=True)
class WorkflowEvidence:
    """The latest run of one trusted workflow file for the head commit."""

    path: str
    event: str
    head_sha: str
    status: str
    jobs: dict[str, str]  # job name -> conclusion ("" while running)
    url: str = ""  # the run's page on GitHub, for the status comment
    steps: dict[str, dict[str, str]] = field(default_factory=dict)  # job -> step -> conclusion


@dataclass
class PullFacts:
    number: int
    title: str
    draft: bool
    base_ref: str
    head_ref: str
    head_sha: str
    head_repo_is_base_repo: bool
    labels: list[str]
    body: str
    files: list[FileChange]
    files_complete: bool
    ci: WorkflowEvidence | None = None
    claude_review: WorkflowEvidence | None = None
    reviews: list[Review] = field(default_factory=list)
    inline_comments: list[Comment] = field(default_factory=list)
    codex_thumbs_on_request: bool = False  # +1 by Codex on the gate's request naming head_sha
    report_text: str | None = None  # changes/NNN-slug/report.md at the head sha
    plan_text: str | None = None  # changes/NNN-slug/plan.md on main
    proposal_text: str | None = None  # changes/NNN-slug/proposal.md on main
    merged_changes: frozenset[str] = frozenset()  # changes with a merged [build] PR
    grounded_in_main: bool | None = None
    main_changed_since_grounding: list[str] | None = None  # None: could not list completely
    behind_main: bool | None = None  # True: the head does not contain main; None: unknown
    state: str = "open"  # the pull request's state on GitHub: open or closed
    base_sha: str | None = None  # the base commit GitHub reports for the pull request
    pinned_main_sha: str | None = None  # the `main` commit this run pinned and read files at


@dataclass(frozen=True)
class Verdict:
    ready: bool
    change: str | None
    reasons: tuple[str, ...]


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """`**` crosses folders, `*` stays inside one folder. Nothing else is special."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def parse_wall_expected(plan_text: str) -> list[str]:
    """Read the backticked paths listed under `wall_expected:` in a plan.md."""
    patterns: list[str] = []
    in_block = False
    for line in plan_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("wall_expected:"):
            in_block = True
            continue
        if in_block:
            if not stripped:
                if patterns:
                    break
                continue
            if not stripped.startswith("-"):
                break
            found = re.search(r"`([^`]+)`", stripped)
            if found:
                patterns.append(found.group(1))
    return patterns


def _is_wall_path(token: str) -> bool:
    return "/" in token or "." in token


def read_wall_forbidden(plan_text: str) -> tuple[list[str], list[str]]:
    """The one parser for `wall_forbidden`: (paths, problems). Never an empty list by accident.

    Grammar. The section is the `wall_forbidden:` line and the lines after it up to the first
    blank line. It is valid in exactly these shapes:

    - Empty: `wall_forbidden: none` and nothing else in the section.
    - Bullets: any lead-in text (it may name backticked paths), then one bullet per path,
      each `- `path`` with an optional trailing parenthetical note. Once bullets start, every
      later line in the section is a bullet.
    - Prose with paths: lead-in text that names at least one backticked path.

    Every other shape is a problem, and the caller must treat the plan as unreadable: no
    backticked path at all (`wall_forbidden: src/a/**`, `wall_forbidden: plus these:` with the
    list after a blank line), an unmatched backtick, a bullet without a path or with a second
    path, prose after the bullets, a bullet list that starts after a blank line, and a missing
    or repeated header. In a bullet the backticked token is the path; in lead-in text a
    backticked token counts as a path when it contains `/` or `.`.
    """
    lines = plan_text.splitlines()
    at = [i for i, ln in enumerate(lines) if ln.strip().startswith("wall_forbidden:")]
    if not at:
        return [], ["`wall_forbidden:` appears 0 times, expected exactly one"]
    paths, problems = _read_forbidden_section(lines, at[0])
    if len(at) != 1:
        problems.append(f"`wall_forbidden:` appears {len(at)} times, expected exactly one")
    return paths, problems


def parse_wall_forbidden(plan_text: str) -> list[str]:
    """The paths of `read_wall_forbidden`; use that function to learn whether the plan is valid."""
    return read_wall_forbidden(plan_text)[0]


def _read_forbidden_section(lines: list[str], header_at: int) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    header_text = lines[header_at].strip()[len("wall_forbidden:") :].strip()
    following = lines[header_at + 1] if header_at + 1 < len(lines) else None
    if not header_text and (following is None or not following.strip()):
        problems.append(
            "wall_forbidden: is followed by a blank line"
            if following is not None
            else "wall_forbidden: has nothing after it (write `wall_forbidden: none`)"
        )
    end = header_at + 1
    while end < len(lines) and lines[end].strip():
        end += 1
    body = lines[header_at + 1 : end]
    after = end
    while after < len(lines) and not lines[after].strip():
        after += 1
    if end < len(lines) and after < len(lines):
        nxt = lines[after].strip()
        if nxt.startswith("-") and any(_is_wall_path(p) for p in re.findall(r"`([^`]+)`", nxt)):
            problems.append("wall_forbidden continues after a blank line")

    paths: list[str] = []
    for line in [lines[header_at], *body]:
        if line.count("`") % 2:
            problems.append(f"wall_forbidden has an unmatched backtick: {line.strip()[:60]!r}")
        tokens = re.findall(r"`([^`]+)`", line)
        if line.strip().startswith("-") and tokens:
            # A bullet names exactly one path, even one without `/` or `.` (`Dockerfile`).
            paths.append(tokens[0])
        else:
            paths.extend(p for p in tokens if _is_wall_path(p))

    in_bullets = False
    for line in body:
        stripped = line.strip()
        if stripped.startswith("-"):
            in_bullets = True
            problems.extend(_forbidden_bullet_problems(stripped))
        elif in_bullets:
            problems.append(f"prose after the wall_forbidden bullets: {stripped[:60]!r}")

    if header_text == WALL_FORBIDDEN_EMPTY:
        if body:
            problems.append("`wall_forbidden: none` is followed by more text in the same section")
    elif not paths:
        problems.append(
            "wall_forbidden names no backticked path (write each path in backticks, "
            "or write `wall_forbidden: none` for an empty list)"
        )
    return paths, problems


def _forbidden_bullet_problems(stripped: str) -> list[str]:
    tokens = re.findall(r"`([^`]+)`", stripped)
    if not tokens:
        return [f"a wall_forbidden bullet has no backticked path: {stripped[:60]!r}"]
    rest = stripped.split(f"`{tokens[0]}`", 1)[1]
    if "`" in rest and not re.fullmatch(r"\s*\(.*\)\s*", rest):
        return [f"a wall_forbidden bullet has more than one path: {stripped[:60]!r}"]
    return []


def wall_problems(plan_text: str) -> list[str]:
    """Ways a plan's wall could be misread by `parse_wall_expected` or `parse_wall_forbidden`.

    The parsers are forgiving (they stop at the first surprise), which is dangerous for a
    fence: a wrapped line ends `wall_expected` early, and a blank line after `wall_forbidden:`
    makes its list empty. An empty list is written `wall_forbidden: none`; the complete
    grammar is on `read_wall_forbidden`, the one parser for that section. A plan with any
    problem here waits, so the planning thread fixes the plan instead of the gate guessing.
    """
    lines = plan_text.splitlines()
    problems: list[str] = []
    expected_at = [i for i, ln in enumerate(lines) if ln.strip().startswith("wall_expected:")]
    if len(expected_at) != 1:
        problems.append(f"`wall_expected:` appears {len(expected_at)} times, expected exactly one")
    else:
        problems.extend(_wall_expected_problems(lines, expected_at[0]))
    problems.extend(read_wall_forbidden(plan_text)[1])
    return problems


def _wall_expected_problems(lines: list[str], header_at: int) -> list[str]:
    problems: list[str] = []
    paths = 0
    for line in lines[header_at + 1 :]:
        stripped = line.strip()
        if not stripped:
            if paths:
                break
            continue
        if not stripped.startswith("-"):
            problems.append(f"a wrapped or non-bullet line inside wall_expected: {stripped[:60]!r}")
            break
        tokens = re.findall(r"`([^`]+)`", stripped)
        if not tokens:
            problems.append(f"a wall_expected bullet has no backticked path: {stripped[:60]!r}")
            continue
        # One path per bullet. Backticks are allowed after it only inside a trailing
        # parenthetical note, such as "(only to import `X` inside `migrate()`)".
        rest = stripped.split(f"`{tokens[0]}`", 1)[1]
        if "`" in rest and not re.fullmatch(r"\s*\(.*\)\s*", rest):
            problems.append(f"a wall_expected bullet has more than one path: {stripped[:60]!r}")
            continue
        paths += 1
    if not paths:
        problems.append("wall_expected lists no paths")
    return problems


def parse_depends_on(proposal_text: str) -> list[str] | None:
    """The changes named on the `depends_on:` line; [] for `none`; None if the line is missing."""
    found = DEPENDS_RE.search(proposal_text)
    if not found:
        return None
    return CHANGE_RE.findall(found.group("deps"))


def only_ticks(before: str | None, after: str | None) -> bool:
    """True when `after` is `before` with some `- [ ]` boxes turned into `- [x]`, nothing else.

    Whole contents, never a diff (third-pass finding 1: a diff parser can mistake a content
    line starting with `++` or `--` for a file heading). Line count, order, every other
    character, and the trailing newline must be identical.
    """
    if before is None or after is None:
        return False
    if before.endswith("\n") != after.endswith("\n"):
        return False
    old_lines, new_lines = before.split("\n"), after.split("\n")
    if len(old_lines) != len(new_lines):
        return False
    for old, new in zip(old_lines, new_lines, strict=True):
        if old == new:
            continue
        if "- [ ]" not in old or new != old.replace("- [ ]", "- [x]", 1):
            return False
    return True


def only_appends(before: str | None, after: str | None) -> bool:
    """True when `after` is exactly `before` followed by at least one whole new line."""
    if before is None or after is None:
        return False
    if not after.startswith(before) or len(after) <= len(before):
        return False
    if before and not before.endswith("\n") and not after[len(before)].startswith("\n"):
        return False  # the addition would change the old last line
    return after[len(before) :].strip("\n") != ""


def is_blocker_text(text: str) -> bool:
    return bool(BLOCKER_LINE_RE.search(text))


def claude_review_failure_reason(cr: WorkflowEvidence) -> str:
    """Why a non-passing review job fails, in words the builder can act on (third-pass
    finding 3, fourth-pass finding 1).

    The review job has three steps after checkout. The model step runs the review. The check
    step fails when the structured result is missing, names another commit, says the review
    did not complete, or has a blocker count that does not match its findings: an invalid or
    incomplete review, nothing to repair. The blockers step runs only after the check step
    passed and fails exactly when the completed, valid review of this commit has a blocker:
    the repair case, with the findings printed as `severity | location | summary` lines in
    that step's log and as inline comments on the pull request. Only the full chain (model
    passed, check passed, blockers failed) is reported as a finding; every other shape
    (authentication, timeout, cancelled, skipped, invalid output) is "did not complete".
    """
    state = cr.jobs.get(REVIEW_JOB) or "missing"
    steps = cr.steps.get(REVIEW_JOB, {})
    where = f" (run {cr.url})" if cr.url else ""
    model = steps.get(REVIEW_MODEL_STEP)
    check = steps.get(REVIEW_CHECK_STEP)
    blockers = steps.get(REVIEW_BLOCKERS_STEP)
    if model == "success" and check == "success" and blockers == "failure":
        return (
            "the Claude review of the head commit completed and found a problem: read the "
            f"`{REVIEW_BLOCKERS_STEP}` step's log and the inline comments{where}"
        )
    # Name how the job ended when GitHub says so: a cancelled or timed-out run just needs a
    # new run, which is different from a run that failed before it could review.
    ended = f"the job ended `{state}`" if state in NOT_COMPLETED_CONCLUSIONS else f"job `{state}`"
    return (
        f"the Claude review of the head commit did not complete or was invalid ({ended}, "
        f"model step `{model or 'missing'}`, check step `{check or 'missing'}`); nothing to "
        f"repair from it until a run completes{where}"
    )


def wall_reasons(pr: PullFacts, change: str) -> list[str]:
    reasons: list[str] = []
    if pr.plan_text is None:
        return [f"`changes/{change}/plan.md` is missing on `main`"]
    expected = parse_wall_expected(pr.plan_text)
    if not expected:
        return ["the plan's `wall_expected` list is empty or unreadable"]
    forbidden = parse_wall_forbidden(pr.plan_text)
    tasks_path = f"changes/{change}/tasks.md"
    report_path = f"changes/{change}/report.md"
    allowed = [p for p in expected if p != tasks_path] + [report_path]
    if not pr.files_complete:
        reasons.append("the list of changed files is incomplete, so the wall cannot be checked")
    if not pr.files:
        reasons.append("the pull request changes no files")
    for f in pr.files:
        if f.path == tasks_path:
            if f.status != "modified" or not only_ticks(f.before, f.after):
                reasons.append(f"`{tasks_path}` changes more than ticked boxes")
            continue
        if f.path == QUESTIONS_PATH:
            if f.status != "modified" or not only_appends(f.before, f.after):
                reasons.append(f"`{QUESTIONS_PATH}` may only gain lines")
            continue
        sides = [f.path] + ([f.previous_path] if f.previous_path else [])
        if f.status in ("renamed", "copied") and not f.previous_path:
            reasons.append(f"`{f.path}` was renamed or copied from an unknown path")
        for path in sides:
            if matches_any(path, STANDING_FORBIDDEN):
                reasons.append(f"`{path}` is standing-forbidden")
            elif matches_any(path, forbidden):
                reasons.append(f"`{path}` is in the plan's `wall_forbidden`")
            elif not matches_any(path, allowed):
                reasons.append(f"`{path}` is outside the change's wall")
    return reasons


def evaluate(pr: PullFacts) -> Verdict:
    reasons: list[str] = []

    title = TITLE_RE.match(pr.title)
    change = title.group("change") if title else None
    if change is None:
        return Verdict(False, None, ("title is not `[build] NNN-slug`",))
    if pr.state != "open":
        reasons.append("pull request is not open")
    if pr.draft:
        reasons.append("pull request is a draft")
    if pr.base_ref != "main":
        reasons.append(f"base is `{pr.base_ref}`, not `main`")
    # Every file read from `main` (proposal, plan, grounding, whole-file comparisons) was read
    # at the pinned commit, so the pull request must sit on that same commit. A newer base may
    # have revoked the ratification or changed the wall; only a fresh scan can tell.
    if pr.base_sha is None or pr.pinned_main_sha is None or pr.base_sha != pr.pinned_main_sha:
        reasons.append(BASE_MOVED_REASON)
    if pr.head_ref != f"build/{change}":
        reasons.append(f"head branch is `{pr.head_ref}`, expected `build/{change}`")
    if not pr.head_repo_is_base_repo:
        reasons.append("head branch is in another repository")
    if HOLD_LABEL in pr.labels:
        reasons.append("the `hold` label is set")

    # Authorized to build at all (read from main, never from the pull request).
    wall = parse_wall_expected(pr.plan_text) if pr.plan_text else []
    if pr.proposal_text is None:
        reasons.append(f"`changes/{change}/proposal.md` is missing on `main`")
    else:
        if not RATIFIED_RE.search(pr.proposal_text):
            reasons.append("the proposal on `main` is not `status: ratified`")
        deps = parse_depends_on(pr.proposal_text)
        if deps is None:
            reasons.append("the proposal has no `depends_on:` line")
        else:
            for dep in deps:
                if dep not in pr.merged_changes:
                    reasons.append(f"dependency `{dep}` has no merged `[build]` pull request")
    if pr.plan_text is not None and not GROUNDED_RE.search(pr.plan_text):
        reasons.append("the plan on `main` has no `grounded_at` commit")
    elif pr.plan_text is not None:
        if pr.grounded_in_main is not True:
            reasons.append("`main` does not contain the plan's `grounded_at` commit")
        if pr.main_changed_since_grounding is None:
            reasons.append("could not list what `main` changed since `grounded_at`")
        else:
            moved = [p for p in pr.main_changed_since_grounding if matches_any(p, wall)]
            if moved:
                reasons.append(f"`main` changed `{moved[0]}` in this wall since `grounded_at`")

    # Current with main: the head must contain the pinned main commit, whatever main changed
    # (third-pass finding 2: dependencies, workflows, and specs sit outside the wall but decide
    # what CI and the reviews test, so evidence from an older base does not count).
    if pr.behind_main is None:
        reasons.append("could not tell whether the branch contains the current `main`")
    elif pr.behind_main:
        reasons.append(BEHIND_MAIN_REASON)

    # CI, from the trusted CI workflow's run for this exact commit.
    sha = pr.head_sha
    if pr.ci is None:
        reasons.append("no CI run for the head commit")
    elif pr.ci.path != CI_WORKFLOW or pr.ci.event != "pull_request" or pr.ci.head_sha != sha:
        reasons.append("the CI evidence is not a pull request run of `ci.yml` for the head commit")
    elif pr.ci.status != "completed":
        # A run in progress is not a failure: one line, no per-job "missing" noise.
        reasons.append(
            f"CI is still running on the head commit (status `{pr.ci.status or 'unknown'}`)"
        )
    else:
        for job in REQUIRED_CI_JOBS:
            conclusion = pr.ci.jobs.get(job)
            if conclusion != "success":
                reasons.append(f"CI job `{job}` is `{conclusion or 'missing'}` on the head commit")

    # Claude review, from the trusted review workflow's run for this exact commit.
    cr = pr.claude_review
    if cr is None:
        reasons.append("no Claude review run for the head commit")
    elif cr.path != REVIEW_WORKFLOW or cr.event != "pull_request" or cr.head_sha != sha:
        reasons.append("the Claude review evidence is not a trusted run for the head commit")
    elif cr.status != "completed":
        reasons.append(
            "the Claude review of the head commit is still running "
            f"(status `{cr.status or 'unknown'}`)"
        )
    elif cr.jobs.get(REVIEW_JOB) != "success":
        reasons.append(claude_review_failure_reason(cr))

    # Codex review, bound to this exact commit. Dismissed and pending reviews are not evidence
    # that Codex finished, but a blocker written on this commit still blocks in any state.
    codex_reviews = codex_reviews_on_head(pr)
    if not codex_reviews and not pr.codex_thumbs_on_request:
        reasons.append(NO_CODEX_REASON)
    if any(is_blocker_text(t) or CODEX_BADGE_RE.search(t) for t in codex_blocker_texts(pr)):
        reasons.append("a Codex finding on the head commit is a blocker (blocker, P0, or P1)")
    if any(r.state == "CHANGES_REQUESTED" for r in codex_reviews):
        reasons.append("Codex requested changes on the head commit")

    if pr.report_text is None:
        reasons.append(f"`changes/{change}/report.md` is missing at the head commit")
    else:
        if not VERDICT_RE.search(pr.report_text):
            reasons.append("the report's verdict is not exactly `Verdict: BUILT`")
        if PLACEHOLDER_RE.search(pr.report_text):
            reasons.append("the report still has a placeholder")

    if not WALL_SECTION_RE.search(pr.body or ""):
        reasons.append("the pull request body has no `## Wall check` section")

    if pr.plan_text is not None:
        reasons.extend(f"the plan's wall is unreadable: {p}" for p in wall_problems(pr.plan_text))
    reasons.extend(wall_reasons(pr, change))
    return Verdict(not reasons, change, tuple(reasons))


def codex_request_body(sha: str) -> str:
    return f"@codex review\n\nMerge gate: no Codex review exists yet for head commit {sha}."


def codex_blocker_texts(pr: PullFacts) -> list[str]:
    """Everything Codex wrote on the head commit, whatever the review's state.

    Completion evidence and blocker collection are separate (phase 1 review, finding 4):
    dismissing a review is not an authorized way to clear its findings, so a blocker in a
    dismissed or pending Codex review of this commit still blocks, exactly as before the
    completion filter existed.
    """
    return [
        r.body for r in pr.reviews if r.author in CODEX_LOGINS and r.commit_id == pr.head_sha
    ] + [
        c.body
        for c in pr.inline_comments
        if c.author in CODEX_LOGINS and c.commit_id == pr.head_sha
    ]


def codex_reviews_on_head(pr: PullFacts) -> list[Review]:
    """Codex's own reviews of the head commit that prove it finished: not dismissed or pending."""
    return [
        r
        for r in pr.reviews
        if r.author in CODEX_LOGINS
        and r.commit_id == pr.head_sha
        and r.state in CODEX_REVIEW_STATES
    ]


def should_request_codex(
    pr: PullFacts, verdict: Verdict, existing_comments: list[tuple[str, str]]
) -> bool:
    """Ask Codex once per head commit, and only when its review is the one thing missing.

    The complete verdict must have no reason except the missing Codex review: an open, ready
    pull request on the pinned `main` whose dependencies, grounding, wall, CI, Claude review,
    report, and body are all fine. A Codex review of anything else is wasted, because it
    cannot yet enable a merge. `existing_comments` is every issue comment on the pull request
    as (author, body); only the gate's own request comments count as an earlier request
    (anyone else can post the same text).
    """
    if verdict.reasons != (NO_CODEX_REASON,):
        return False
    request = codex_request_body(pr.head_sha)
    return not any(author == GATE_LOGIN and body == request for author, body in existing_comments)


def status_body(verdict: Verdict, sha: str) -> str:
    lines = [STATUS_MARKER, f"**Merge gate** for head commit `{sha}`: waiting on", ""]
    lines.extend(f"- {r}" for r in verdict.reasons)
    return "\n".join(lines)


def has_codex_evidence(facts: PullFacts) -> bool:
    return facts.codex_thumbs_on_request or bool(codex_reviews_on_head(facts))


def wait_for_codex_many(
    gh: GitHub,
    waiting: dict[int, tuple[dict, PullFacts]],
    main_sha: str,
    merged: frozenset[str],
    budget_seconds: float,
    poll_seconds: float,
    sleep=None,
    clock=None,
) -> tuple[dict[int, PullFacts], dict[int, Exception], set[int]]:
    """After the gate asks Codex, stay in this run until Codex answers or the budget is spent.

    Codex answers a request comment minutes later, and nothing on GitHub starts a workflow
    run when it does (a review from a bot is not an event this gate may safely run on, and
    the schedule event is unreliable). So the run that asked keeps polling, re-reading each
    waiting pull request from scratch (`gather`), and returns the newest facts for each.

    There is ONE deadline for the whole run, `budget_seconds` from now, shared by every
    waiting pull request: each round sleeps once and polls all of them, so two pull requests
    never cost two budgets. A pull request leaves the wait when Codex answered, when its head
    commit changed (the request named the old head), when it kept changing while it was read
    (a builder pushing: ordinary waiting, listed in the third result and never a fault), or
    when reading it failed (the error is returned for the caller to count as a fault). The
    caller re-evaluates the facts of every pull request except the moving ones, whose older
    facts must never be judged again.
    """
    sleep = sleep or _sleep
    clock = clock or _clock
    deadline = clock() + budget_seconds
    latest = {number: facts for number, (_, facts) in waiting.items()}
    errors: dict[int, Exception] = {}
    moving: set[int] = set()
    pending = set(waiting)
    while pending and clock() < deadline:
        sleep(min(poll_seconds, deadline - clock()))
        for number in sorted(pending):
            try:
                latest[number] = gather(gh, waiting[number][0], main_sha, merged)
            except PullKeptChanging:
                moving.add(number)
                pending.discard(number)
                continue
            except Exception as err:  # noqa: BLE001 (fail closed: this PR waits, run has a fault)
                errors[number] = err
                pending.discard(number)
                continue
            head_moved = latest[number].head_sha != waiting[number][1].head_sha
            if has_codex_evidence(latest[number]) or head_moved:
                pending.discard(number)
    return latest, errors, moving


def wait_for_codex(
    gh: GitHub,
    pr: dict,
    main_sha: str,
    merged: frozenset[str],
    facts: PullFacts,
    budget_seconds: float,
    poll_seconds: float,
    sleep=None,
    clock=None,
) -> PullFacts:
    """`wait_for_codex_many` for one pull request.

    Raises if reading it failed, and raises `PullKeptChanging` when it kept changing (the
    caller treats that as waiting); the older facts are never returned as current.
    """
    latest, errors, moving = wait_for_codex_many(
        gh,
        {facts.number: (pr, facts)},
        main_sha,
        merged,
        budget_seconds,
        poll_seconds,
        sleep,
        clock,
    )
    if facts.number in errors:
        raise errors[facts.number]
    if facts.number in moving:
        raise PullKeptChanging(KEPT_CHANGING_REASON)
    return latest[facts.number]


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _clock() -> float:
    import time

    return time.monotonic()


# ---------------------------------------------------------------------------------------------
# GitHub plumbing. Everything below only fetches facts or performs the merge.
# ---------------------------------------------------------------------------------------------


class GitHub:
    def __init__(self, token: str, repo: str, api: str = "https://api.github.com") -> None:
        self.token = token
        self.repo = repo
        self.api = api

    def request(self, method: str, path: str, body: dict | None = None):
        url = path if path.startswith("http") else f"{self.api}/repos/{self.repo}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def paged(self, path: str, key: str | None = None, limit_pages: int = 30) -> list:
        items: list = []
        sep = "&" if "?" in path else "?"
        for page in range(1, limit_pages + 1):
            got = self.request("GET", f"{path}{sep}per_page=100&page={page}")
            batch = got[key] if key else got
            items.extend(batch)
            if len(batch) < 100:
                break
        return items

    def file_at(self, path: str, ref: str) -> str | None:
        try:
            got = self.request("GET", f"/contents/{path}?ref={ref}")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise
        return base64.b64decode(got["content"]).decode("utf-8")

    def compare_files(self, base: str, head: str) -> tuple[dict | None, list[str] | None]:
        try:
            got = self.request("GET", f"/compare/{base}...{head}")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None, None
            raise
        files = got.get("files") or []
        if len(files) >= MAX_COMPARE_FILES:
            return got, None
        paths = [f["filename"] for f in files]
        paths += [f["previous_filename"] for f in files if f.get("previous_filename")]
        return got, paths

    def latest_run(self, workflow_path: str, sha: str) -> WorkflowEvidence | None:
        name = workflow_path.rsplit("/", 1)[-1]
        runs = self.request("GET", f"/actions/workflows/{name}/runs?head_sha={sha}&per_page=50")
        runs = [r for r in runs.get("workflow_runs", []) if r.get("event") == "pull_request"]
        if not runs:
            return None
        run = max(runs, key=lambda r: (r["id"], r.get("run_attempt", 1)))
        jobs = self.request("GET", f"/actions/runs/{run['id']}/jobs?per_page=100")
        return WorkflowEvidence(
            path=(run.get("path") or "").split("@", 1)[0],
            event=run.get("event", ""),
            head_sha=run.get("head_sha", ""),
            status=run.get("status", ""),
            jobs={j["name"]: (j.get("conclusion") or "") for j in jobs.get("jobs", [])},
            url=run.get("html_url") or "",
            steps={
                j["name"]: {s["name"]: (s.get("conclusion") or "") for s in j.get("steps") or []}
                for j in jobs.get("jobs", [])
            },
        )


def login(obj: dict) -> str:
    return (obj.get("user") or {}).get("login", "")


class PullKeptChanging(Exception):
    """The head or base of a pull request moved on every attempt to read it."""


def _pin(full: dict) -> tuple[str, str]:
    """The two commits a reading is about: the head and the base the pull request sits on."""
    return (
        ((full.get("head") or {}).get("sha") or ""),
        ((full.get("base") or {}).get("sha") or ""),
    )


def gather(gh: GitHub, pr: dict, main_sha: str, merged: frozenset[str]) -> PullFacts:
    """Read one pull request as a single snapshot (process review finding 1).

    Only `pr["number"]` comes from the caller (the list response may be stale: a label or a
    push can land between the list and now). Every other fact comes from ONE fresh
    `GET /pulls/{number}`, then the files, evidence, and report are read for that head. After
    reading, the pull request is fetched again; if its head or base moved meanwhile, the
    reading starts over, at most MAX_SNAPSHOT_ATTEMPTS times, then PullKeptChanging.
    """
    number = pr["number"]
    for _ in range(MAX_SNAPSHOT_ATTEMPTS):
        full = gh.request("GET", f"/pulls/{number}")
        facts = _gather_snapshot(gh, number, full, main_sha, merged)
        if _pin(gh.request("GET", f"/pulls/{number}")) == _pin(full):
            return facts
    raise PullKeptChanging(KEPT_CHANGING_REASON)


def _gather_snapshot(
    gh: GitHub, number: int, full: dict, main_sha: str, merged: frozenset[str]
) -> PullFacts:
    sha = full["head"]["sha"]
    title = TITLE_RE.match(full["title"])
    change = title.group("change") if title else None

    raw_files = gh.paged(f"/pulls/{number}/files")
    shape_checked = {f"changes/{change}/tasks.md", QUESTIONS_PATH} if change else set()
    files = []
    for f in raw_files:
        path = f["filename"]
        before = after = None
        if path in shape_checked:
            # Whole contents, both sides. `main` is the true base only because rule 3 refuses
            # a head that does not contain the pinned main commit.
            before = gh.file_at(path, main_sha)
            after = gh.file_at(path, sha)
        files.append(
            FileChange(path, f.get("status", ""), f.get("previous_filename"), before, after)
        )
    complete = len(raw_files) == full.get("changed_files", -1) and len(raw_files) < MAX_FILES

    comments = gh.paged(f"/issues/{number}/comments")
    request = [
        c
        for c in comments
        if login(c) == GATE_LOGIN and (c.get("body") or "") == codex_request_body(sha)
    ]
    thumbs = False
    for c in request:
        reactions = gh.paged(f"/issues/comments/{c['id']}/reactions")
        thumbs = thumbs or any(
            login(r) in CODEX_LOGINS and r.get("content") == "+1" for r in reactions
        )

    plan = gh.file_at(f"changes/{change}/plan.md", main_sha) if change else None
    grounded = GROUNDED_RE.search(plan) if plan else None
    in_main, since_grounding = None, None
    if grounded:
        cmp, since_grounding = gh.compare_files(grounded.group("sha"), main_sha)
        in_main = cmp is not None and cmp.get("status") in ("ahead", "identical")

    # Behind main? The head contains the pinned main commit exactly when comparing
    # main...head reports main as the merge base (status `ahead` or `identical`).
    behind: bool | None = None
    cmp, _ = gh.compare_files(main_sha, sha)
    if cmp is not None:
        base = (cmp.get("merge_base_commit") or {}).get("sha")
        if base:
            behind = base != main_sha

    return PullFacts(
        number=number,
        title=full["title"],
        draft=bool(full.get("draft")),
        base_ref=full["base"]["ref"],
        head_ref=full["head"]["ref"],
        head_sha=sha,
        head_repo_is_base_repo=(full["head"].get("repo") or {}).get("full_name") == gh.repo,
        labels=[label["name"] for label in full.get("labels") or []],
        body=full.get("body") or "",
        files=files,
        files_complete=complete,
        ci=gh.latest_run(CI_WORKFLOW, sha),
        claude_review=gh.latest_run(REVIEW_WORKFLOW, sha),
        reviews=[
            Review(login(r), r.get("state", ""), r.get("body") or "", r.get("commit_id", ""))
            for r in gh.paged(f"/pulls/{number}/reviews")
        ],
        inline_comments=[
            Comment(login(c), c.get("body") or "", c.get("commit_id"))
            for c in gh.paged(f"/pulls/{number}/comments")
        ],
        codex_thumbs_on_request=thumbs,
        report_text=gh.file_at(f"changes/{change}/report.md", sha) if change else None,
        plan_text=plan,
        proposal_text=gh.file_at(f"changes/{change}/proposal.md", main_sha) if change else None,
        merged_changes=merged,
        grounded_in_main=in_main,
        main_changed_since_grounding=since_grounding,
        behind_main=behind,
        state=full.get("state", ""),
        base_sha=(full.get("base") or {}).get("sha"),
        pinned_main_sha=main_sha,
    )


def merged_changes(gh: GitHub) -> frozenset[str]:
    closed = gh.paged("/pulls?state=closed&base=main&sort=updated&direction=desc")
    out = set()
    for p in closed:
        title = TITLE_RE.match(p.get("title", ""))
        if title and p.get("merged_at"):
            out.add(title.group("change"))
    return frozenset(out)


def post_status(gh: GitHub, number: int, body: str) -> None:
    """Keep exactly one gate status comment per pull request, edited in place."""
    comments = gh.paged(f"/issues/{number}/comments")
    mine = [
        c for c in comments if login(c) == GATE_LOGIN and STATUS_MARKER in (c.get("body") or "")
    ]
    if mine:
        if mine[0].get("body") != body:
            gh.request("PATCH", f"/issues/comments/{mine[0]['id']}", {"body": body})
    else:
        gh.request("POST", f"/issues/{number}/comments", {"body": body})


def summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    print(text)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def metadata_changes(fresh: dict, facts: PullFacts, repo: str) -> list[str]:
    """What changed in a fresh `GET /pulls/{n}` since `facts` were judged ready to act on.

    Everything `evaluate()` relies on that one GET can show: open state, draft flag, hold
    label, title (the change identity), base branch and base commit, head commit and
    repository, and the required `## Wall check` section of the body.
    """
    changed = []
    if fresh.get("state") != "open":
        changed.append(f"it is `{fresh.get('state')}`")
    if fresh.get("draft"):
        changed.append("it is a draft")
    if HOLD_LABEL in [label.get("name") for label in fresh.get("labels") or []]:
        changed.append("the `hold` label was added")
    if fresh.get("title") != facts.title:
        changed.append("the title changed")
    base = fresh.get("base") or {}
    if base.get("ref") != facts.base_ref:
        changed.append(f"the base branch is now `{base.get('ref')}`")
    if base.get("sha") != facts.base_sha:
        changed.append("the base commit changed")
    head = fresh.get("head") or {}
    if head.get("sha") != facts.head_sha:
        changed.append("the head commit changed")
    if (head.get("repo") or {}).get("full_name") != repo:
        changed.append("the head repository changed")
    if not WALL_SECTION_RE.search(fresh.get("body") or ""):
        changed.append("the body has no `## Wall check` section")
    return changed


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    dry_run = os.environ.get("MERGE_GATE_DRY_RUN", "") == "true"
    codex_budget = float(os.environ.get("MERGE_GATE_CODEX_WAIT_SECONDS", "600"))
    codex_poll = float(os.environ.get("MERGE_GATE_CODEX_POLL_SECONDS", "30"))
    gh = GitHub(token, repo)

    pulls = gh.paged("/pulls?state=open&base=main&sort=created&direction=asc")
    candidates = [p for p in pulls if TITLE_RE.match(p["title"])]
    lines = ["# Merge gate", ""]
    if not candidates:
        summary(lines + ["No open `[build]` pull requests."])
        return 0

    # Pin the base once per run: every file read from main uses this exact commit.
    main_sha = gh.request("GET", "/branches/main")["commit"]["sha"]
    merged_set = merged_changes(gh)
    lines.append(f"Evaluated against `main` at `{main_sha}`.")
    lines.append("")
    run = _Run(gh, lines, dry_run, main_sha, merged_set)

    # Phase 1: read and judge every candidate before doing anything slow or irreversible.
    judged: list[tuple[dict, PullFacts, Verdict]] = []
    for pr in candidates:
        try:
            facts = gather(gh, pr, main_sha, merged_set)
            verdict = evaluate(facts)
        except PullKeptChanging:
            # The builder is pushing; that is waiting, not a fault of the gate.
            lines.append(f"- #{pr['number']} waits: {KEPT_CHANGING_REASON}.")
            continue
        except Exception as err:  # noqa: BLE001 (fail closed: an error means this PR waits)
            run.fault(f"- #{pr['number']} waits: the gate could not read it ({err!r}).")
            continue
        judged.append((pr, facts, verdict))

    # Phase 2: merge at most one ready pull request, oldest first, before any waiting.
    not_ready: list[tuple[dict, PullFacts, Verdict]] = []
    for pr, facts, verdict in judged:
        if not verdict.ready:
            not_ready.append((pr, facts, verdict))
        else:
            run.merge_or_defer(facts)

    # Phase 3: for the rest, keep the status comment current and ask Codex where it can help.
    # After a merge in this run, every other head is behind the new `main` and needs a sync
    # first, so a Codex review of it would be thrown away: no requests then.
    requested: dict[int, tuple[dict, PullFacts]] = {}
    for pr, facts, verdict in not_ready:
        if dry_run:
            continue
        run.status(facts, status_body(verdict, facts.head_sha))
        if run.merged_one:
            continue
        try:
            comments = gh.paged(f"/issues/{facts.number}/comments")
            existing = [(login(c), c.get("body") or "") for c in comments]
            if should_request_codex(facts, verdict, existing):
                # The facts are from before the other candidates were read: look again.
                changed = metadata_changes(gh.request("GET", f"/pulls/{facts.number}"), facts, repo)
                if changed:
                    lines.append(
                        f"- #{facts.number}: not asking Codex, it changed ({'; '.join(changed)})."
                    )
                    continue
                gh.request(
                    "POST",
                    f"/issues/{facts.number}/comments",
                    {"body": codex_request_body(facts.head_sha)},
                )
                lines.append(f"- #{facts.number}: asked Codex to review the head commit.")
                requested[facts.number] = (pr, facts)
        except Exception as err:  # noqa: BLE001 (fail closed: this PR waits)
            run.fault(f"- #{facts.number}: the Codex request failed ({err!r}).")

    # Phase 4: one wait for the whole run. Only worth it while no pull request has merged
    # (one merge per run), because only a merge could come out of it.
    final = {facts.number: (facts, verdict) for _, facts, verdict in not_ready}
    before = {facts.number: verdict.reasons for _, facts, verdict in not_ready}
    if requested and codex_budget > 0 and not run.merged_one:
        latest, errors, moving = wait_for_codex_many(
            gh, requested, main_sha, merged_set, codex_budget, codex_poll
        )
        for number, (_, old_facts) in requested.items():
            if number in errors:
                run.fault(f"  - #{number}: the wait failed ({errors[number]!r}).")
                continue
            if number in moving:
                # A builder is pushing: waiting, not a fault. The facts from before the wait
                # are old, so they are neither judged again nor listed as current.
                lines.append(f"  - #{number} waits: {KEPT_CHANGING_REASON}.")
                final.pop(number, None)
                continue
            facts = latest[number]
            verdict = evaluate(facts)
            final[number] = (facts, verdict)
            lines.append(
                f"  - #{number}: Codex answered; re-evaluated."
                if has_codex_evidence(facts)
                else f"  - #{number}: no Codex answer within {codex_budget:.0f} seconds."
            )
            if verdict.ready:
                run.merge_or_defer(facts)
            elif facts.head_sha != old_facts.head_sha or verdict.reasons != before[number]:
                run.status(facts, status_body(verdict, facts.head_sha))

    for facts, verdict in final.values():
        if not verdict.ready:
            lines.append(f"- #{facts.number} `{facts.title}` waits:")
            lines.extend(f"  - {r}" for r in verdict.reasons)

    if run.faults:
        lines.append(f"Faults: {run.faults}")
    summary(lines)
    return 1 if run.faults else 0


class _Run:
    """What one run of the gate has done so far: its summary lines, merges, and faults.

    A fault is the gate failing to do something it set out to do (read a pull request, post
    a comment, ask Codex, merge, clean up). A pull request that is plainly waiting is not.
    """

    def __init__(
        self,
        gh: GitHub,
        lines: list[str],
        dry_run: bool,
        main_sha: str,
        merged: frozenset[str],
    ) -> None:
        self.gh = gh
        self.lines = lines
        self.dry_run = dry_run
        self.main_sha = main_sha  # the commit this run pinned; the merge is judged against it
        self.merged = merged
        self.merged_one = False  # a merge happened (or, in a dry run, would have)
        self.faults = 0

    def fault(self, line: str) -> None:
        self.faults += 1
        self.lines.append(line)

    def status(self, facts: PullFacts, body: str) -> None:
        if self.dry_run:
            return
        try:
            post_status(self.gh, facts.number, body)
        except Exception as err:  # noqa: BLE001 (fail closed: this PR waits)
            self.fault(f"  - #{facts.number}: posting the status comment failed ({err!r}).")

    def merge_or_defer(self, facts: PullFacts) -> None:
        if self.merged_one:
            self.lines.append(
                f"- #{facts.number} is ready; it merges on the next run (one per run)."
            )
        elif self.dry_run:
            self.lines.append(f"- #{facts.number} is ready (dry run, not merged).")
            self.merged_one = True
        else:
            self.merged_one = self.merge(facts)

    def merge(self, facts: PullFacts) -> bool:
        """Judge the pull request again, merge it if nothing changed, and clean up.

        The facts that made it ready may be minutes old (other candidates were read after it,
        and Codex may have been waited for). So the merge first re-gathers and re-evaluates
        the pull request against the still-pinned `main` (fresh metadata, CI, Claude review,
        Codex reviews), requires the same head and base as the facts that were judged ready,
        and only then re-reads the pull request once more, as close to the merge request as a
        GET allows. True if merged.
        """
        gh, lines, n = self.gh, self.lines, facts.number
        try:
            again = gather(gh, {"number": n}, self.main_sha, self.merged)
        except PullKeptChanging:
            lines.append(f"- #{n} waits: {KEPT_CHANGING_REASON}; it was not merged.")
            return False
        except Exception as err:  # noqa: BLE001
            self.fault(
                f"- #{n} was ready but the gate could not re-read it before merging ({err!r})."
            )
            return False
        verdict = evaluate(again)
        stale = []
        if not verdict.ready:
            stale.append("it is no longer ready: " + "; ".join(verdict.reasons))
        if again.head_sha != facts.head_sha:
            stale.append("the head commit changed")
        if again.base_sha != facts.base_sha:
            stale.append("the base commit changed")
        if stale:
            lines.append(
                f"- #{n} changed before merge ({'; '.join(stale)}); it waits for the next run."
            )
            if not verdict.ready:
                self.status(again, status_body(verdict, again.head_sha))
            return False
        try:
            fresh = gh.request("GET", f"/pulls/{n}")
        except Exception as err:  # noqa: BLE001
            self.fault(
                f"- #{n} was ready but the gate could not re-read it before merging ({err!r})."
            )
            return False
        changed = metadata_changes(fresh, facts, gh.repo)
        if changed:
            lines.append(
                f"- #{n} changed before merge ({'; '.join(changed)}); it waits for the next run."
            )
            return False
        try:
            result = gh.request(
                "PUT",
                f"/pulls/{n}/merge",
                {
                    "merge_method": "squash",
                    "sha": facts.head_sha,
                    "commit_title": f"{facts.title} (#{n})",
                },
            )
        except Exception as err:  # noqa: BLE001 (the merge may or may not have happened)
            why = _why(err)
            self.fault(f"- #{n} was ready but GitHub refused the merge ({why}).")
            self.status(facts, _merge_problem_body(facts, f"GitHub refused the merge ({why})"))
            return False
        if not (isinstance(result, dict) and result.get("merged") is True and result.get("sha")):
            self.fault(f"- #{n} was ready but GitHub did not confirm a merge.")
            self.status(facts, _merge_problem_body(facts, "GitHub did not confirm the merge"))
            return False
        lines.append(f"- #{n} `{facts.title}` merged as `{result['sha']}`.")
        self.status(
            facts,
            f"{STATUS_MARKER}\n**Merge gate** for head commit `{facts.head_sha}`: "
            f"merged as `{result['sha']}`.",
        )
        try:
            gh.request("DELETE", f"/git/refs/heads/{facts.head_ref}")
        except Exception as err:  # noqa: BLE001
            self.fault(f"  - branch `{facts.head_ref}` not deleted ({_why(err)}).")
        try:
            gh.request("POST", "/actions/workflows/ci.yml/dispatches", {"ref": "main"})
            lines.append("  - CI dispatched on `main` (a merge made by this token starts no run).")
        except Exception as err:  # noqa: BLE001
            self.fault(f"  - CI dispatch on `main` failed ({_why(err)}).")
        return True


def _why(err: Exception) -> object:
    return err.code if isinstance(err, urllib.error.HTTPError) else repr(err)


def _merge_problem_body(facts: PullFacts, problem: str) -> str:
    return (
        f"{STATUS_MARKER}\n**Merge gate** for head commit `{facts.head_sha}`: ready, but "
        f"{problem}. The gate tries again on its next run."
    )


if __name__ == "__main__":
    sys.exit(main())
