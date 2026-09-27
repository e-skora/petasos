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


def parse_wall_forbidden(plan_text: str) -> list[str]:
    """Every backticked path in the `wall_forbidden:` paragraph (up to the next blank line)."""
    patterns: list[str] = []
    in_block = False
    for line in plan_text.splitlines():
        if line.strip().startswith("wall_forbidden:"):
            in_block = True
        elif in_block and not line.strip():
            break
        if in_block:
            patterns.extend(p for p in re.findall(r"`([^`]+)`", line) if "/" in p or "." in p)
    return patterns


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
    return (
        f"the Claude review of the head commit did not complete or was invalid (job `{state}`, "
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
    if pr.draft:
        reasons.append("pull request is a draft")
    if pr.base_ref != "main":
        reasons.append(f"base is `{pr.base_ref}`, not `main`")
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
    elif cr.jobs.get(REVIEW_JOB) != "success":
        reasons.append(claude_review_failure_reason(cr))

    # Codex review, bound to this exact commit.
    codex_reviews = [r for r in pr.reviews if r.author in CODEX_LOGINS and r.commit_id == sha]
    if not codex_reviews and not pr.codex_thumbs_on_request:
        reasons.append("no Codex review for the head commit")
    codex_texts = [r.body for r in codex_reviews] + [
        c.body for c in pr.inline_comments if c.author in CODEX_LOGINS and c.commit_id == sha
    ]
    if any(is_blocker_text(t) or CODEX_BADGE_RE.search(t) for t in codex_texts):
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

    reasons.extend(wall_reasons(pr, change))
    return Verdict(not reasons, change, tuple(reasons))


def codex_request_body(sha: str) -> str:
    return f"@codex review\n\nMerge gate: no Codex review exists yet for head commit {sha}."


def should_request_codex(pr: PullFacts, verdict: Verdict, existing_bodies: list[str]) -> bool:
    """Ask Codex once per head commit, only when CI is green and its review is the gap."""
    if "no Codex review for the head commit" not in verdict.reasons:
        return False
    if pr.ci is None or any(pr.ci.jobs.get(j) != "success" for j in REQUIRED_CI_JOBS):
        return False
    return codex_request_body(pr.head_sha) not in existing_bodies


def status_body(verdict: Verdict, sha: str) -> str:
    lines = [STATUS_MARKER, f"**Merge gate** for head commit `{sha}`: waiting on", ""]
    lines.extend(f"- {r}" for r in verdict.reasons)
    return "\n".join(lines)


def has_codex_evidence(facts: PullFacts) -> bool:
    return facts.codex_thumbs_on_request or any(
        r.author in CODEX_LOGINS and r.commit_id == facts.head_sha for r in facts.reviews
    )


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
    """After the gate asks Codex, stay in this run until Codex answers or the budget is spent.

    Codex answers a request comment minutes later, and nothing on GitHub starts a workflow
    run when it does (a review from a bot is not an event this gate may safely run on, and
    the schedule event is unreliable). So the run that asked keeps polling, re-reading the
    pull request each time, and returns the newest facts. The caller re-evaluates them.
    """
    sleep = sleep or _sleep
    clock = clock or _clock
    deadline = clock() + budget_seconds
    while clock() < deadline:
        sleep(poll_seconds)
        facts = gather(gh, pr, main_sha, merged)
        if has_codex_evidence(facts):
            break
    return facts


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


def gather(gh: GitHub, pr: dict, main_sha: str, merged: frozenset[str]) -> PullFacts:
    number = pr["number"]
    sha = pr["head"]["sha"]
    title = TITLE_RE.match(pr["title"])
    change = title.group("change") if title else None

    full = gh.request("GET", f"/pulls/{number}")
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
        title=pr["title"],
        draft=bool(pr.get("draft")),
        base_ref=pr["base"]["ref"],
        head_ref=pr["head"]["ref"],
        head_sha=sha,
        head_repo_is_base_repo=(pr["head"].get("repo") or {}).get("full_name") == gh.repo,
        labels=[label["name"] for label in pr.get("labels", [])],
        body=pr.get("body") or "",
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
    merged_one = False
    for pr in candidates:
        try:
            facts = gather(gh, pr, main_sha, merged_set)
            verdict = evaluate(facts)
        except Exception as err:  # noqa: BLE001 (fail closed: an error means this PR waits)
            lines.append(f"- #{pr['number']} waits: the gate could not read it ({err!r}).")
            continue
        if not verdict.ready and not dry_run:
            try:
                post_status(gh, facts.number, status_body(verdict, facts.head_sha))
                bodies = [c.get("body") or "" for c in gh.paged(f"/issues/{facts.number}/comments")]
                if should_request_codex(facts, verdict, bodies):
                    gh.request(
                        "POST",
                        f"/issues/{facts.number}/comments",
                        {"body": codex_request_body(facts.head_sha)},
                    )
                    lines.append(f"- #{facts.number}: asked Codex to review the head commit.")
                    if codex_budget > 0:
                        facts = wait_for_codex(
                            gh, pr, main_sha, merged_set, facts, codex_budget, codex_poll
                        )
                        verdict = evaluate(facts)
                        lines.append(
                            "  - Codex answered; re-evaluated."
                            if has_codex_evidence(facts)
                            else f"  - no Codex answer within {codex_budget:.0f} seconds."
                        )
                        if not verdict.ready:
                            post_status(gh, facts.number, status_body(verdict, facts.head_sha))
            except Exception as err:  # noqa: BLE001 (fail closed: this PR waits)
                lines.append(f"  - the status, the Codex request, or the wait failed ({err!r}).")
        if not verdict.ready:
            lines.append(f"- #{facts.number} `{facts.title}` waits:")
            lines.extend(f"  - {r}" for r in verdict.reasons)
            continue
        if merged_one:
            lines.append(f"- #{facts.number} is ready; it merges on the next run (one per run).")
            continue
        if dry_run:
            lines.append(f"- #{facts.number} is ready (dry run, not merged).")
            merged_one = True
            continue
        try:
            result = gh.request(
                "PUT",
                f"/pulls/{facts.number}/merge",
                {
                    "merge_method": "squash",
                    "sha": facts.head_sha,
                    "commit_title": f"{facts.title} (#{facts.number})",
                },
            )
        except urllib.error.HTTPError as err:
            lines.append(f"- #{facts.number} was ready but GitHub refused the merge ({err.code}).")
            continue
        if not (isinstance(result, dict) and result.get("merged") is True and result.get("sha")):
            lines.append(f"- #{facts.number} was ready but GitHub did not confirm a merge.")
            continue
        merged_one = True
        lines.append(f"- #{facts.number} `{facts.title}` merged as `{result['sha']}`.")
        try:
            gh.request("DELETE", f"/git/refs/heads/{facts.head_ref}")
        except urllib.error.HTTPError as err:
            lines.append(f"  - branch `{facts.head_ref}` not deleted ({err.code}).")
        try:
            gh.request("POST", "/actions/workflows/ci.yml/dispatches", {"ref": "main"})
            lines.append("  - CI dispatched on `main` (a merge made by this token starts no run).")
        except urllib.error.HTTPError as err:
            lines.append(f"  - CI dispatch on `main` failed ({err.code}).")

    summary(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
