"""Checks the scheduled builder's structured result against independent evidence.

Why this exists: on 2026-10-02 a builder run lasted 83 minutes, reported success, pushed only
its empty claim commit to a `build/` branch, and opened no pull request. Nothing noticed.

The builder must end by returning a structured result (see `claude-builder.yml`). This script
decides whether that result is true. It runs twice in each builder run, and never in the job
where the builder runs (phase 1 third-delta review, D1):

    builder_result.py --snapshot-output
        In the `snapshot` job, on its own runner, from `main` at the run's commit, before the
        builder starts. Records the open `[build]` pull requests, the `build/*` branches, the
        merge gate's status comment on each pull request, commit ages, repair counts, and the
        change queue read from the checkout. From those facts it derives what the builder was
        supposed to do (`derive_expected`). The whole record, bound to the repository, run, and
        attempt, becomes the job output `state`, which no later job can change.

    builder_result.py --verify
        In the `verify` job, on a fresh runner, from `main` at the run's commit, after the
        builder job ends however it ends. Reads the starting record from the `snapshot` job's
        output (environment `BEFORE_STATE`), the builder's result (`RESULT`), and the builder
        job's conclusion (`BUILD_JOB_RESULT`), reads GitHub again, and runs `check()`. Exits 1
        with one plain sentence per problem.

Everything that decides is pure (`derive_expected`, `check`), so `tests/test_builder_result.py`
tests every rule without a network. Reading GitHub goes through the `gh` command line tool,
which reads its token from `GH_TOKEN`. Facts that cannot be read stop the script with exit 1:
missing evidence is a failed verification, never a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 2
ACTIONS = ("started", "repaired", "synced", "no-op", "blocked")
NO_OP_REASONS = (
    "nothing_eligible",
    "waiting_on_reviews",
    "waiting_on_planning",
    "repairs_exhausted",
    "held",
)
RESULT_KEYS = ("action", "pull_request", "change", "head", "reason")
HOLD_LABEL = "hold"
BRANCH_PREFIX = "build/"
TITLE_RE = re.compile(r"^\[build\] (?P<change>\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CHANGE_RE = re.compile(r"\b\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*")

# The builder's own rules (the prompt in claude-builder.yml, sections A to C).
REPAIR_AGE_HOURS = 4
STALE_CLAIM_HOURS = 6
MAX_REPAIRS = 2
REPAIR_PREFIX = "repair: "

# The merge gate's status comment (merge_gate.py: GATE_LOGIN, STATUS_MARKER, status_body).
GATE_LOGIN = "github-actions[bot]"
STATUS_MARKER = "<!-- petasos-merge-gate -->"
STATUS_HEAD_RE = re.compile(r"\*\*Merge gate\*\* for head commit `([0-9a-f]{40})`")

# Gate reasons the builder must repair (prompt section A): a failing CI job, a completed Claude
# review with a finding, a Codex blocker, a report problem, a wall problem, behind `main`.
REPAIR_REASON_RES = tuple(
    re.compile(pattern)
    for pattern in (
        r"^CI job `[^`]+` is `(failure|timed_out|cancelled|startup_failure)` on the head commit",
        r"^the Claude review of the head commit completed and found a problem",
        r"^a Codex finding on the head commit is a blocker",
        r"^Codex requested changes on the head commit",
        r"^`changes/[^`]+/report\.md` is missing at the head commit",
        r"^the report's verdict is not exactly",
        r"^the report still has a placeholder",
        r"is standing-forbidden$",
        r"is in the plan's `wall_forbidden`$",
        r"is outside the change's wall$",
        r"changes more than ticked boxes$",
        r"may only gain lines$",
        r"was renamed or copied from an unknown path$",
        r"^the pull request body has no `## Wall check` section",
        r"^the branch is behind `main`; merge `main` into it$",
    )
)


# ---------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _age_hours(value: object, now: datetime) -> float | None:
    when = _parse_time(value)
    return None if when is None else (now - when).total_seconds() / 3600


def repair_reasons(reasons: Sequence[str]) -> list[str]:
    """The gate reasons that make a pull request a repair case."""
    return [r for r in reasons if any(p.search(r) for p in REPAIR_REASON_RES)]


def parse_status_comment(body: str) -> tuple[str | None, list[str]]:
    """The head commit a gate status comment names, and its reason bullets."""
    found = STATUS_HEAD_RE.search(body)
    reasons = [line[2:].strip() for line in body.splitlines() if line.startswith("- ")]
    return (found.group(1) if found else None), reasons


def parse_change(change: str, proposal: str | None, plan: str | None) -> dict:
    """The queue facts the builder reads from one `changes/NNN-slug/` folder (prompt C)."""
    proposal = proposal or ""
    plan = plan or ""
    dep_line = re.search(r"^depends_on:[ \t]*(.*)$", proposal, re.MULTILINE)
    if dep_line is None:
        depends_on = None
    elif dep_line.group(1).strip().lower().startswith("none"):
        depends_on = []
    else:
        depends_on = CHANGE_RE.findall(dep_line.group(1))
    return {
        "change": change,
        "ratified": bool(re.search(r"^status:[ \t]*ratified[ \t]*$", proposal, re.MULTILINE)),
        "depends_on": depends_on,
        "grounded": bool(
            re.search(r"^grounded_at:[ \t]*`?[0-9a-f]{7,40}`?(\s|$)", plan, re.MULTILINE)
        ),
        "lane_ok": bool(
            re.search(r"^lane:[ \t]*(claude|codex or claude)[ \t]*$", plan, re.MULTILINE)
        ),
    }


def derive_expected(start: Mapping, now: datetime) -> dict:
    """What the builder was supposed to do, from the trusted starting facts alone.

    Mirrors the builder prompt: A (repair the first pull request that needs it, or mark it a
    draft once it has used both repairs), B (stale claims may be deleted), C (build the
    lowest-numbered eligible change). Facts that are needed but missing go in `unverifiable`.
    """
    pulls = start.get("pulls", [])
    branches = start.get("branches", {})
    branch_dates = start.get("branch_dates", {})
    merged = set(start.get("merged_changes", []))
    open_changes = {m.group("change") for p in pulls if (m := TITLE_RE.match(p["title"]))}
    open_branches = {p["branch"] for p in pulls}
    unverifiable: list[str] = []

    stale = []
    for branch in sorted(branches):
        change = branch[len(BRANCH_PREFIX) :]
        if branch in open_branches or change in merged:
            continue
        age = _age_hours(branch_dates.get(branch), now)
        if age is None:
            unverifiable.append(f"the age of branch {branch} is unknown")
        elif age > STALE_CLAIM_HOURS:
            stale.append(branch)

    repair_due: list[int] = []
    exhaust_due: list[int] = []
    for pull in pulls:
        if pull["draft"] or pull.get("status_head") != pull["head"]:
            continue
        if not repair_reasons(pull.get("status_reasons", [])):
            continue
        age = _age_hours(pull.get("head_date"), now)
        repairs = pull.get("repair_commits")
        if age is None or not isinstance(repairs, int):
            unverifiable.append(f"whether pull request {pull['number']} needed a repair")
            continue
        if age <= REPAIR_AGE_HOURS:
            continue
        (exhaust_due if repairs >= MAX_REPAIRS else repair_due).append(pull["number"])

    eligible = None
    for item in sorted(start.get("changes", []), key=lambda c: c["change"]):
        change = item["change"]
        deps = item.get("depends_on")
        if not (item.get("ratified") and item.get("grounded") and item.get("lane_ok")):
            continue
        if deps is None or any(dep not in merged for dep in deps):
            continue
        if change in merged or change in open_changes:
            continue
        branch = f"{BRANCH_PREFIX}{change}"
        if branch in branches and branch not in stale:
            continue
        eligible = change
        break

    return {
        "eligible": eligible,
        "repair_due": sorted(repair_due),
        "exhaust_due": sorted(exhaust_due),
        "stale_branches": stale,
        "unverifiable": unverifiable,
    }


# ---------------------------------------------------------------------------------------------
# Pure checks
# ---------------------------------------------------------------------------------------------


def parse_result(raw: object) -> tuple[dict | None, str | None]:
    """Turn the builder's raw result into a dict, or say in one sentence what is wrong.

    Accepts the raw string the action puts in `structured_output`, or an already parsed object.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "The builder returned no structured result."
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None, "The builder's structured result is not valid JSON."
    if not isinstance(value, dict):
        return None, "The builder's structured result is not a JSON object."
    keys = set(value)
    if keys != set(RESULT_KEYS):
        missing = sorted(set(RESULT_KEYS) - keys)
        extra = sorted(keys - set(RESULT_KEYS))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        return None, "The builder's structured result has the wrong keys: " + "; ".join(
            detail
        ) + "."
    problems: list[str] = []
    if value["action"] not in ACTIONS:
        problems.append("action is not one of the allowed values")
    pull = value["pull_request"]
    if pull is not None and (isinstance(pull, bool) or not isinstance(pull, int) or pull < 1):
        problems.append("pull_request is not a positive integer or null")
    if value["change"] is not None and not isinstance(value["change"], str):
        problems.append("change is not a string or null")
    if value["head"] is not None and not (
        isinstance(value["head"], str) and SHA_RE.match(value["head"])
    ):
        problems.append("head is not 40 lowercase hex characters or null")
    if not isinstance(value["reason"], str):
        problems.append("reason is not a string")
    if problems:
        return None, "The builder's structured result is invalid: " + "; ".join(problems) + "."
    is_noop = value["action"] == "no-op"
    nulls = [k for k in ("pull_request", "change", "head") if value[k] is None]
    if is_noop and len(nulls) != 3:
        return None, (
            "The builder's structured result is invalid: a no-op must have pull_request, "
            "change, and head all null."
        )
    if not is_noop and nulls:
        return None, (
            "The builder's structured result is invalid: "
            + ", ".join(nulls)
            + f" must not be null for {value['action']}."
        )
    return value, None


def _pulls_by_number(state: Mapping) -> dict[int, dict]:
    return {p["number"]: p for p in state.get("pulls", [])}


def _final_pull(number: int, after: Mapping) -> tuple[dict | None, str | None]:
    """The reported pull request as it ended: open (from the open list) or MERGED/CLOSED."""
    open_pull = _pulls_by_number(after).get(number)
    if open_pull is not None:
        return open_pull, "OPEN"
    reported = after.get("reported")
    if isinstance(reported, Mapping) and reported.get("number") == number:
        return dict(reported), reported.get("state")
    return None, None


def _check_shape(number: int, pull: Mapping, change: str, head: str) -> list[str]:
    problems = []
    title = f"[build] {change}"
    if pull["title"] != title:
        problems.append(f'Pull request {number} is not titled "{title}".')
    if pull["branch"] != f"{BRANCH_PREFIX}{change}":
        problems.append(f"Pull request {number} does not come from branch {BRANCH_PREFIX}{change}.")
    if pull["head"] != head:
        problems.append(
            f"Pull request {number} has head {pull['head']}, not the {head} the builder reported."
        )
    return problems


def _check_action(before: Mapping, after: Mapping, result: Mapping) -> list[str]:
    action = result["action"]
    expected = before["expected"]
    before_pulls = _pulls_by_number(before)
    number = result["pull_request"]
    change = result["change"]
    head = result["head"]
    if action == "no-op":
        return _check_no_op(before, after, result)

    pull, final = _final_pull(number, after)
    if pull is None:
        return [
            (
                f"The builder said it {action} pull request {number}, "
                "but no open or merged pull request has that number."
            )
        ]
    if final == "CLOSED":
        return [f"Pull request {number} was closed without merging."]
    if final not in ("OPEN", "MERGED"):
        return [f"Pull request {number} ended in an unknown state ({final})."]
    problems = _check_shape(number, pull, change, head)
    old = before_pulls.get(number)

    if action == "started":
        if old is not None:
            problems.append(
                f"The builder said it started pull request {number}, "
                "but that pull request already existed before the run."
            )
        due = expected["repair_due"] + expected["exhaust_due"]
        if due:
            problems.append(
                f"The builder started a build, but pull request {due[0]} needed "
                "a repair or a draft first."
            )
        elif expected["eligible"] != change:
            problems.append(
                f"The builder started {change}, but the eligible change at the start was "
                f"{expected['eligible'] or 'none'}."
            )
    elif action in ("repaired", "synced"):
        if old is None:
            problems.append(
                f"The builder said it {action} pull request {number}, "
                "but that pull request was not open before the run."
            )
        elif pull["head"] == old["head"]:
            problems.append(
                f"The builder said it {action} pull request {number}, "
                "but its head commit did not change."
            )
        if number in expected["exhaust_due"]:
            problems.append(
                f"Pull request {number} had used both repairs; it should have been marked "
                "a draft, not repaired again."
            )
        elif number not in expected["repair_due"]:
            problems.append(f"Pull request {number} did not need a repair at the start of the run.")
    else:  # blocked
        if final != "OPEN" or not pull["draft"]:
            problems.append(
                f"The builder said pull request {number} is blocked, but it is not an open draft."
            )
        elif old is not None and old["draft"] and old["head"] == pull["head"]:
            problems.append(
                f"Pull request {number} was already a draft at this head before the run; "
                "nothing was blocked in this run (that is a no-op, waiting_on_planning)."
            )
    return problems


def _check_no_op(before: Mapping, after: Mapping, result: Mapping) -> list[str]:
    problems: list[str] = []
    expected = before["expected"]
    before_pulls = _pulls_by_number(before)
    after_pulls = _pulls_by_number(after)

    if expected["eligible"]:
        problems.append(
            f"The builder reported no-op, but {expected['eligible']} was eligible to build."
        )
    for number in expected["repair_due"]:
        problems.append(f"The builder reported no-op, but pull request {number} needed a repair.")
    for number in expected["exhaust_due"]:
        problems.append(
            f"The builder reported no-op, but pull request {number} had used both repairs "
            "and needed to be marked a draft."
        )

    for number, pull in after_pulls.items():
        if number not in before_pulls:
            problems.append(
                f"The builder reported no-op, but pull request {number} was opened during the run."
            )
        elif pull["head"] != before_pulls[number]["head"]:
            problems.append(
                f"The builder reported no-op, but pull request {number} got a new head commit."
            )

    before_branches = before.get("branches", {})
    after_branches = after.get("branches", {})
    for branch in sorted(set(after_branches) - set(before_branches)):
        problems.append(f"The builder reported no-op, but created branch {branch}.")
    for branch in sorted(set(before_branches) & set(after_branches)):
        if before_branches[branch] != after_branches[branch]:
            problems.append(f"The builder reported no-op, but branch {branch} moved.")

    # Deleting a stale claim branch is allowed (prompt B). The merge gate may merge a pull
    # request and delete its branch while the builder runs; that deletion is not the builder's.
    merged_since = {int(n) for n, s in after.get("closed", {}).items() if s == "MERGED"}
    merged_branches = {p["branch"] for n, p in before_pulls.items() if n in merged_since}
    stale = set(expected.get("stale_branches", []))
    for branch in sorted(set(before_branches) - set(after_branches)):
        if branch in merged_branches or branch in stale:
            continue
        problems.append(
            f"The builder reported no-op, but deleted branch {branch}, which was not a stale claim."
        )

    reason = result["reason"]
    pulls = list(after_pulls.values())
    drafts = [p for p in pulls if p["draft"]]
    held = [p for p in pulls if HOLD_LABEL in p.get("labels", [])]
    waiting = [p for p in pulls if not p["draft"] and HOLD_LABEL not in p.get("labels", [])]
    exhausted = [
        p
        for p in before_pulls.values()
        if p["draft"]
        and isinstance(p.get("repair_commits"), int)
        and p["repair_commits"] >= MAX_REPAIRS
    ]
    if reason not in NO_OP_REASONS:
        problems.append(f'The no-op reason "{reason}" is not one of the allowed reasons.')
    elif reason == "waiting_on_planning" and not drafts:
        problems.append(
            "The builder reported waiting_on_planning, but no open build pull request is a draft."
        )
    elif reason == "held" and not held:
        problems.append(
            "The builder reported held, but no open build pull request has the hold label."
        )
    elif reason == "waiting_on_reviews" and not waiting:
        problems.append(
            "The builder reported waiting_on_reviews, but no open build pull request is "
            "ready for review, unheld, and free of repair reasons."
        )
    elif reason == "repairs_exhausted" and not exhausted:
        problems.append(
            "The builder reported repairs_exhausted, but no open build pull request is a draft "
            f"with {MAX_REPAIRS} repair commits."
        )
    elif reason == "nothing_eligible" and waiting:
        problems.append(
            "The builder reported nothing_eligible, "
            "but an open build pull request is neither a draft nor held."
        )
    return problems


def check(
    before: Mapping, after: Mapping, result: object, build_job: str | None = "success"
) -> list[str]:
    """Return one plain sentence per problem; an empty list means the run passes."""
    problems: list[str] = []
    if build_job != "success":
        problems.append(
            f"The builder job ended as `{build_job or 'unknown'}`, not success "
            "(a timeout, a cancellation, or a lost runner ends here too)."
        )
    # Always applies, even when the result is missing: a branch created during the run with
    # no pull request is the 2026-10-02 failure. A pull request merged during the run counts.
    pull_branches = {p["branch"] for p in after.get("pulls", [])}
    reported = after.get("reported")
    if isinstance(reported, Mapping) and reported.get("state") == "MERGED":
        pull_branches.add(reported.get("branch"))
    before_branches = before.get("branches", {})
    for branch in sorted(after.get("branches", {})):
        if branch not in before_branches and branch not in pull_branches:
            problems.append(f"The builder claimed {branch} but opened no pull request.")

    expected = before.get("expected")
    if not isinstance(expected, Mapping):
        problems.append("The starting record has no queue evidence, so nothing can be verified.")
        return problems
    for gap in expected.get("unverifiable", []):
        problems.append(f"Could not verify the run: {gap}.")

    parsed, why = parse_result(result)
    if parsed is None:
        problems.insert(0, why or "The builder's structured result is unusable.")
        return problems
    problems.extend(_check_action(before, after, parsed))
    return problems


# ---------------------------------------------------------------------------------------------
# Reading GitHub and the checkout
# ---------------------------------------------------------------------------------------------


def build_snapshot(pull_rows: Sequence[Mapping], ref_rows: Sequence[Mapping]) -> dict:
    """Shape raw `gh` output into the open build pull requests and the build branches.

    `pull_rows` come from `gh pr list --json number,title,headRefName,headRefOid,isDraft,labels`;
    `ref_rows` are `{"ref": "refs/heads/build/...", "sha": "..."}`.
    """
    pulls = [_shape_pull(row) for row in pull_rows if TITLE_RE.match(row.get("title", ""))]
    pulls.sort(key=lambda p: p["number"])
    prefix = f"refs/heads/{BRANCH_PREFIX}"
    branches = {
        row["ref"][len("refs/heads/") :]: row["sha"]
        for row in ref_rows
        if row["ref"].startswith(prefix)
    }
    return {"pulls": pulls, "branches": dict(sorted(branches.items()))}


def _shape_pull(row: Mapping) -> dict:
    labels = sorted(
        (label["name"] if isinstance(label, Mapping) else str(label))
        for label in row.get("labels") or []
    )
    return {
        "number": row["number"],
        "title": row["title"],
        "branch": row["headRefName"],
        "head": row["headRefOid"],
        "draft": bool(row["isDraft"]),
        "labels": labels,
    }


def _decode_stream(text: str) -> list:
    """Decode what `gh api --paginate --jq '.[] | ...'` prints: one JSON value after another."""
    decoder = json.JSONDecoder()
    items: list = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        value, pos = decoder.raw_decode(text, pos)
        items.append(value)
    return items


Runner = Callable[[Sequence[str]], str]


def run_gh(args: Sequence[str]) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {done.stderr.strip()}")
    return done.stdout


def read_state(repo: str, runner: Runner = run_gh) -> dict:
    pulls = json.loads(
        runner(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "200",
                "--json",
                "number,title,headRefName,headRefOid,isDraft,labels",
            ]
        )
        or "[]"
    )
    refs = _decode_stream(
        runner(
            [
                "api",
                "--paginate",
                f"repos/{repo}/git/matching-refs/heads/{BRANCH_PREFIX}?per_page=100",
                "--jq",
                ".[] | {ref: .ref, sha: .object.sha}",
            ]
        )
    )
    return build_snapshot(pulls, refs)


def _commit_date(repo: str, sha: str, runner: Runner) -> str:
    out = runner(["api", f"repos/{repo}/commits/{sha}", "--jq", ".commit.committer.date"]).strip()
    if _parse_time(out) is None:
        raise ValueError(f"commit {sha} has no readable date")
    return out


def _gate_status(repo: str, number: int, runner: Runner) -> tuple[str | None, list[str]]:
    comments = _decode_stream(
        runner(
            [
                "api",
                "--paginate",
                f"repos/{repo}/issues/{number}/comments?per_page=100",
                "--jq",
                ".[] | {login: .user.login, body: .body, updated: .updated_at}",
            ]
        )
    )
    mine = [
        c
        for c in comments
        if c.get("login") == GATE_LOGIN and (c.get("body") or "").startswith(STATUS_MARKER)
    ]
    if not mine:
        return None, []
    latest = max(mine, key=lambda c: c.get("updated") or "")
    return parse_status_comment(latest["body"])


def _repair_commits(repo: str, number: int, runner: Runner) -> int:
    rows = _decode_stream(
        runner(
            [
                "api",
                "--paginate",
                f"repos/{repo}/pulls/{number}/commits?per_page=100",
                "--jq",
                ".[] | {m: .commit.message}",
            ]
        )
    )
    return sum(1 for row in rows if str(row.get("m", "")).startswith(REPAIR_PREFIX))


def read_queue(root: Path) -> list[dict]:
    """The change queue as the builder reads it, from the trusted checkout at `root`."""
    items = []
    for proposal in sorted((root / "changes").glob("[0-9][0-9][0-9]-*/proposal.md")):
        folder = proposal.parent
        plan = folder / "plan.md"
        items.append(
            parse_change(
                folder.name,
                proposal.read_text(encoding="utf-8"),
                plan.read_text(encoding="utf-8") if plan.exists() else None,
            )
        )
    return items


def read_start(repo: str, root: Path, runner: Runner = run_gh, now: datetime | None = None) -> dict:
    """Every fact the starting record holds, then what the builder was supposed to do."""
    now = now or datetime.now(UTC)
    start = read_state(repo, runner)
    for pull in start["pulls"]:
        pull["status_head"], pull["status_reasons"] = _gate_status(repo, pull["number"], runner)
        pull["head_date"] = _commit_date(repo, pull["head"], runner)
        pull["repair_commits"] = _repair_commits(repo, pull["number"], runner)
    start["branch_dates"] = {
        branch: _commit_date(repo, sha, runner) for branch, sha in start["branches"].items()
    }
    merged = json.loads(
        runner(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "merged",
                "--search",
                "[build] in:title",
                "--limit",
                "500",
                "--json",
                "title",
            ]
        )
        or "[]"
    )
    start["merged_changes"] = sorted(
        {m.group("change") for row in merged if (m := TITLE_RE.match(row.get("title", "")))}
    )
    start["changes"] = read_queue(root)
    start["taken_at"] = now.isoformat()
    start["expected"] = derive_expected(start, now)
    return start


def closed_since(before: Mapping, after: Mapping, repo: str, runner: Runner = run_gh) -> dict:
    """The final state (MERGED or CLOSED) of each build pull request open before but not after."""
    still_open = {p["number"] for p in after.get("pulls", [])}
    closed: dict[str, str] = {}
    for pull in before.get("pulls", []):
        if pull["number"] in still_open:
            continue
        out = runner(
            ["pr", "view", str(pull["number"]), "--repo", repo, "--json", "state", "--jq", ".state"]
        )
        closed[str(pull["number"])] = out.strip()
    return closed


def read_reported(repo: str, number: int, runner: Runner = run_gh) -> dict:
    """The pull request the result names, open or not, with its final state."""
    row = json.loads(
        runner(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "number,title,headRefName,headRefOid,isDraft,labels,state",
            ]
        )
    )
    shaped = _shape_pull(row)
    shaped["state"] = row.get("state")
    return shaped


# ---------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------


def _binding(env: Mapping[str, str]) -> dict:
    return {
        "repo": env.get("GITHUB_REPOSITORY", ""),
        "run_id": env.get("GITHUB_RUN_ID", ""),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
    }


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Runner = run_gh,
    root: Path | None = None,
    now: datetime | None = None,
) -> int:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot-output", action="store_true", help="record the start")
    mode.add_argument("--verify", action="store_true", help="check the result")
    args = parser.parse_args(argv)
    binding = _binding(env)
    if not all(binding.values()):
        print(
            "GITHUB_REPOSITORY, GITHUB_RUN_ID, and GITHUB_RUN_ATTEMPT must be set.", file=sys.stderr
        )
        return 1
    repo = binding["repo"]

    if args.snapshot_output:
        out_path = env.get("GITHUB_OUTPUT", "")
        if not out_path:
            print("GITHUB_OUTPUT is not set.", file=sys.stderr)
            return 1
        try:
            start = read_start(repo, root or Path.cwd(), runner, now)
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as err:
            print(f"Could not read the starting state: {err}", file=sys.stderr)
            return 1
        record = {"schema": SCHEMA, "binding": binding, **start}
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write("state=" + json.dumps(record, separators=(",", ":")) + "\n")
        expected = start["expected"]
        print(
            f"Recorded {len(start['pulls'])} open build pull request(s) and "
            f"{len(start['branches'])} build branch(es). Eligible change: "
            f"{expected['eligible'] or 'none'}; repair due: {expected['repair_due'] or 'none'}; "
            f"draft due: {expected['exhaust_due'] or 'none'}."
        )
        return 0

    problems: list[str] = []
    try:
        before = json.loads(env.get("BEFORE_STATE") or "null")
    except json.JSONDecodeError:
        before = None
    if not isinstance(before, dict) or before.get("schema") != SCHEMA:
        problems.append("There is no trusted starting record from the snapshot job.")
    elif before.get("binding") != binding:
        problems.append("The starting record belongs to another repository, run, or attempt.")
    else:
        try:
            after = read_state(repo, runner)
            after["closed"] = closed_since(before, after, repo, runner)
            parsed, _ = parse_result(env.get("RESULT"))
            if parsed is not None and parsed["pull_request"] is not None:
                after["reported"] = read_reported(repo, parsed["pull_request"], runner)
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as err:
            problems.append(f"Could not read the repository after the run: {err}")
        else:
            problems.extend(check(before, after, env.get("RESULT"), env.get("BUILD_JOB_RESULT")))
    if problems and env.get("BUILD_JOB_RESULT") not in (None, "success"):
        line = f"The builder job ended as `{env.get('BUILD_JOB_RESULT')}`"
        if not any(p.startswith(line) for p in problems):
            problems.append(line + ".")
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print("The builder's result matches the independent evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
