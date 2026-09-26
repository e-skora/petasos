"""The merge gate: squash-merges a builder pull request when a script can prove it is ready.

DECISIONS.md D-015. No model runs here. The scheduled workflow `.github/workflows/merge-gate.yml`
calls `main()`. Everything that decides is in `evaluate()`, a pure function over facts fetched
from the GitHub REST API, so `tests/test_merge_gate.py` can test every rule without a network.

A builder pull request merges only when ALL of these hold on its current head commit:

1. Title is `[build] NNN-slug`, head branch is `build/NNN-slug` in this repository, base is
   `main`, it is not a draft, and it has no `hold` label.
2. The CI jobs `python`, `demo`, and `private-identifiers` each finished with `success`.
3. The Claude review posted a summary comment for this exact commit that says `Blockers: 0`,
   and none of its inline comments on this commit is a blocker.
4. The Codex review posted on this exact commit (a review, or a thumbs-up reaction made after
   this commit's CI started) and none of its findings on this commit is a blocker.
5. `changes/NNN-slug/report.md` at this commit says `Verdict: BUILT` and has no placeholders.
6. The pull request body has a `## Wall check` section (the builder's own check).
7. The gate's own wall check passes: every changed file matches the change's `wall_expected`
   list, read from `plan.md` on `main` (trusted), and none is standing-forbidden.

What counts as a blocker (fail closed): a finding line whose severity is `blocker` (the
AGENTS.md section 6 shape), or a Codex `P0` or `P1` badge, or a review whose state is
`CHANGES_REQUESTED`. Anything else waits and the reasons are written to the run summary.

The merge uses the head commit's sha, so a push that lands between the check and the merge
makes GitHub refuse it. One pull request merges per run; the next run sees the new `main`.
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

REQUIRED_CHECKS = ("python", "demo", "private-identifiers")
CLAUDE_LOGINS = frozenset({"claude[bot]"})
CODEX_LOGINS = frozenset({"chatgpt-codex-connector[bot]"})
HOLD_LABEL = "hold"

TITLE_RE = re.compile(r"^\[build\] (?P<change>\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*)$")
VERDICT_RE = re.compile(r"(?m)^Verdict:\s*BUILT\s*$")
PLACEHOLDER_RE = re.compile(r"<!--|TODO|TBD|\[fill")
WALL_SECTION_RE = re.compile(r"(?mi)^##\s*Wall check\s*$")
SUMMARY_HEAD_RE = re.compile(r"(?m)^Petasos review: claude\s*$")
BLOCKERS_ZERO_RE = re.compile(r"(?m)^Blockers:\s*0\s*$")
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
    "changes/*/proposal.md",
    "changes/*/spec.md",
    "changes/*/plan.md",
    ".github/**",
    "tests/test_no_private_identifiers.py",
    "tests/test_merge_gate.py",
    "review/**",
)


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
class Reaction:
    author: str
    content: str
    created_at: str  # ISO 8601, compared as text (GitHub always returns UTC with a Z)


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
    changed_files: list[str]
    check_conclusions: dict[str, str]  # latest conclusion per check name on the head sha
    ci_started_at: str | None  # earliest start of a required check on the head sha
    issue_comments: list[Comment] = field(default_factory=list)
    inline_comments: list[Comment] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    reactions: list[Reaction] = field(default_factory=list)
    report_text: str | None = None  # changes/NNN-slug/report.md at the head sha
    plan_text: str | None = None  # changes/NNN-slug/plan.md on main


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


def is_blocker_text(text: str) -> bool:
    return bool(BLOCKER_LINE_RE.search(text))


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

    for name in REQUIRED_CHECKS:
        conclusion = pr.check_conclusions.get(name)
        if conclusion != "success":
            reasons.append(f"CI job `{name}` is `{conclusion or 'missing'}` on the head commit")

    sha = pr.head_sha
    claude_summaries = [
        c
        for c in pr.issue_comments
        if c.author in CLAUDE_LOGINS
        and SUMMARY_HEAD_RE.search(c.body)
        and re.search(rf"(?m)^Commit:\s*{re.escape(sha)}\s*$", c.body)
    ]
    if not claude_summaries:
        reasons.append("no Claude review summary for the head commit")
    elif not any(BLOCKERS_ZERO_RE.search(c.body) for c in claude_summaries):
        reasons.append("the Claude review summary for the head commit reports blockers")
    claude_inline = [
        c for c in pr.inline_comments if c.author in CLAUDE_LOGINS and c.commit_id == sha
    ]
    if any(is_blocker_text(c.body) for c in claude_inline):
        reasons.append("a Claude inline finding on the head commit is a blocker")
    if any(
        r.author in CLAUDE_LOGINS and r.commit_id == sha and r.state == "CHANGES_REQUESTED"
        for r in pr.reviews
    ):
        reasons.append("Claude requested changes on the head commit")

    codex_reviews = [r for r in pr.reviews if r.author in CODEX_LOGINS and r.commit_id == sha]
    codex_thumbs = [
        r
        for r in pr.reactions
        if r.author in CODEX_LOGINS
        and r.content == "+1"
        and pr.ci_started_at is not None
        and r.created_at >= pr.ci_started_at
    ]
    if not codex_reviews and not codex_thumbs:
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

    if pr.plan_text is None:
        reasons.append(f"`changes/{change}/plan.md` is missing on `main`")
    else:
        allowed = parse_wall_expected(pr.plan_text) + [
            f"changes/{change}/tasks.md",
            f"changes/{change}/report.md",
            "changes/QUESTIONS.md",
        ]
        if len(allowed) == 3:
            reasons.append("the plan's `wall_expected` list is empty or unreadable")
        if not pr.changed_files:
            reasons.append("the pull request changes no files")
        for path in pr.changed_files:
            if matches_any(path, STANDING_FORBIDDEN):
                reasons.append(f"`{path}` is standing-forbidden")
            elif not matches_any(path, allowed):
                reasons.append(f"`{path}` is outside the change's wall")

    return Verdict(not reasons, change, tuple(reasons))


def codex_request_body(sha: str) -> str:
    return f"@codex review\n\nMerge gate: no Codex review exists yet for head commit {sha}."


def should_request_codex(pr: PullFacts, verdict: Verdict) -> bool:
    """Ask Codex once per head commit, only when CI is green and its review is the gap.

    Codex reviews a pull request when it opens; whether it re-reviews after later pushes is not
    documented, so the gate asks explicitly. It never asks twice for the same commit.
    """
    if "no Codex review for the head commit" not in verdict.reasons:
        return False
    if any(pr.check_conclusions.get(name) != "success" for name in REQUIRED_CHECKS):
        return False
    body = codex_request_body(pr.head_sha)
    return not any(c.body == body for c in pr.issue_comments)


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

    def paged(self, path: str, key: str | None = None) -> list:
        items: list = []
        sep = "&" if "?" in path else "?"
        for page in range(1, 31):
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


def gather(gh: GitHub, pr: dict) -> PullFacts:
    number = pr["number"]
    sha = pr["head"]["sha"]
    title = TITLE_RE.match(pr["title"])
    change = title.group("change") if title else None

    runs = gh.paged(f"/commits/{sha}/check-runs", key="check_runs")
    latest: dict[str, dict] = {}
    for run in runs:
        name = run["name"]
        if name not in REQUIRED_CHECKS:
            continue
        if name not in latest or run["id"] > latest[name]["id"]:
            latest[name] = run
    conclusions = {name: (run.get("conclusion") or run["status"]) for name, run in latest.items()}
    starts = [run["started_at"] for run in latest.values() if run.get("started_at")]

    def login(obj: dict) -> str:
        return (obj.get("user") or {}).get("login", "")

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
        changed_files=[f["filename"] for f in gh.paged(f"/pulls/{number}/files")],
        check_conclusions=conclusions,
        ci_started_at=min(starts) if starts else None,
        issue_comments=[
            Comment(login(c), c.get("body") or "") for c in gh.paged(f"/issues/{number}/comments")
        ],
        inline_comments=[
            Comment(login(c), c.get("body") or "", c.get("commit_id"))
            for c in gh.paged(f"/pulls/{number}/comments")
        ],
        reviews=[
            Review(login(r), r.get("state", ""), r.get("body") or "", r.get("commit_id", ""))
            for r in gh.paged(f"/pulls/{number}/reviews")
        ],
        reactions=[
            Reaction(login(r), r.get("content", ""), r.get("created_at", ""))
            for r in gh.paged(f"/issues/{number}/reactions")
        ],
        report_text=gh.file_at(f"changes/{change}/report.md", sha) if change else None,
        plan_text=gh.file_at(f"changes/{change}/plan.md", "main") if change else None,
    )


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
    gh = GitHub(token, repo)

    pulls = gh.paged("/pulls?state=open&base=main&sort=created&direction=asc")
    candidates = [p for p in pulls if TITLE_RE.match(p["title"])]
    lines = ["# Merge gate", ""]
    if not candidates:
        summary(lines + ["No open `[build]` pull requests."])
        return 0

    merged = False
    for pr in candidates:
        try:
            facts = gather(gh, pr)
            verdict = evaluate(facts)
        except Exception as err:  # noqa: BLE001 (fail closed: an error means this PR waits)
            lines.append(f"- #{pr['number']} waits: the gate could not read it ({err!r}).")
            continue
        if not verdict.ready:
            lines.append(f"- #{facts.number} `{facts.title}` waits:")
            lines.extend(f"  - {r}" for r in verdict.reasons)
            if should_request_codex(facts, verdict) and not dry_run:
                try:
                    gh.request(
                        "POST",
                        f"/issues/{facts.number}/comments",
                        {"body": codex_request_body(facts.head_sha)},
                    )
                    lines.append("  - asked Codex to review the head commit.")
                except urllib.error.HTTPError as err:
                    lines.append(f"  - asking Codex for a review failed ({err.code}).")
            continue
        if merged:
            lines.append(f"- #{facts.number} is ready; it merges on the next run (one per run).")
            continue
        if dry_run:
            lines.append(f"- #{facts.number} is ready (dry run, not merged).")
            merged = True
            continue
        try:
            gh.request(
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
        merged = True
        lines.append(f"- #{facts.number} `{facts.title}` merged at head {facts.head_sha}.")
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
