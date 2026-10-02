"""Checks the scheduled builder's structured result against what GitHub shows afterward.

Why this exists: on 2026-10-02 a builder run lasted 83 minutes, reported success, pushed only
its empty claim commit to a `build/` branch, and opened no pull request. Nothing noticed.
Now the builder must end by returning a structured result (see `claude-builder.yml`), and a
step that always runs calls this script to compare that result with the state of the repository
before and after the run. The script exits 1, with one plain sentence per problem, when the
result is missing or invalid, when a claimed branch has no pull request, or when what the
builder said it did is not what happened. No model runs here.

Two modes, both used by `claude-builder.yml`:

    builder_result.py --snapshot FILE
        Record the open `[build] NNN-slug` pull requests and the remote `build/*` branches.
    builder_result.py --before FILE --result-env RESULT
        Read the current state the same way and run `check()` against the saved one and the
        result held in the environment variable named RESULT.

Everything that decides is `check()`, a pure function, so `tests/test_builder_result.py` tests
every rule without a network. `main()` talks to GitHub through the `gh` command line tool,
which reads its token from `GH_TOKEN`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence

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


def _check_action(before: Mapping, after: Mapping, result: Mapping) -> list[str]:
    action = result["action"]
    before_pulls = _pulls_by_number(before)
    after_pulls = _pulls_by_number(after)
    number = result["pull_request"]
    change = result["change"]
    head = result["head"]
    title = f"[build] {change}"
    problems: list[str] = []

    if action == "started":
        pull = after_pulls.get(number)
        if pull is None:
            return [
                f"The builder said it started pull request {number}, but no open pull request has that number."
            ]
        if number in before_pulls:
            problems.append(
                f"The builder said it started pull request {number}, "
                "but that pull request already existed before the run."
            )
        if pull["title"] != title:
            problems.append(f'Pull request {number} is not titled "{title}".')
        if pull["branch"] != f"{BRANCH_PREFIX}{change}":
            problems.append(
                f"Pull request {number} does not come from branch {BRANCH_PREFIX}{change}."
            )
        if pull["head"] != head:
            problems.append(
                f"Pull request {number} has head {pull['head']}, not the {head} the builder reported."
            )

    elif action in ("repaired", "synced"):
        old = before_pulls.get(number)
        pull = after_pulls.get(number)
        if old is None or pull is None:
            return [
                (
                    f"The builder said it {action} pull request {number}, "
                    "but that pull request was not open both before and after the run."
                )
            ]
        if pull["title"] != title:
            problems.append(f'Pull request {number} is not titled "{title}".')
        if pull["head"] == old["head"]:
            problems.append(
                f"The builder said it {action} pull request {number}, "
                "but its head commit did not change."
            )
        if pull["head"] != head:
            problems.append(
                f"Pull request {number} has head {pull['head']}, not the {head} the builder reported."
            )

    elif action == "blocked":
        pull = after_pulls.get(number)
        if pull is None:
            problems.append(
                f"The builder said pull request {number} is blocked, "
                "but no open pull request has that number."
            )
        elif not pull["draft"]:
            problems.append(
                f"The builder said pull request {number} is blocked, but it is not a draft."
            )
        elif pull["title"] != title:
            problems.append(f'Pull request {number} is not titled "{title}".')

    else:  # no-op
        problems.extend(_check_no_op(before, after, result))
    return problems


def _check_no_op(before: Mapping, after: Mapping, result: Mapping) -> list[str]:
    problems: list[str] = []
    before_pulls = _pulls_by_number(before)
    after_pulls = _pulls_by_number(after)
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
    # Deleting a stale claim branch (a build branch with no pull request) is allowed.
    # The merge gate may merge a pull request and delete its branch while the builder runs;
    # that deletion is not the builder's.
    merged_since = {int(n) for n, state in after.get("closed", {}).items() if state == "MERGED"}
    had_pull = {p["branch"] for n, p in before_pulls.items() if n not in merged_since}
    for branch in sorted(set(before_branches) - set(after_branches)):
        if branch in had_pull:
            problems.append(
                f"The builder reported no-op, but deleted branch {branch}, "
                "which had an open pull request."
            )

    reason = result["reason"]
    pulls = list(after_pulls.values())
    drafts = [p for p in pulls if p["draft"]]
    held = [p for p in pulls if HOLD_LABEL in p.get("labels", [])]
    ready = [p for p in pulls if not p["draft"]]
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
    elif reason in ("waiting_on_reviews", "repairs_exhausted") and not ready:
        problems.append(
            f"The builder reported {reason}, but no open build pull request is ready for review."
        )
    elif reason == "nothing_eligible" and any(
        not p["draft"] and HOLD_LABEL not in p.get("labels", []) for p in pulls
    ):
        problems.append(
            "The builder reported nothing_eligible, "
            "but an open build pull request is neither a draft nor held."
        )
    return problems


def check(before: Mapping, after: Mapping, result: object) -> list[str]:
    """Return one plain sentence per problem; an empty list means the run passes."""
    problems: list[str] = []

    # Rule 2 always applies, even when the result is missing: a branch created during the run
    # with no open pull request is the 2026-10-02 failure.
    pull_branches = {p["branch"] for p in after.get("pulls", [])}
    before_branches = before.get("branches", {})
    for branch in sorted(after.get("branches", {})):
        if branch not in before_branches and branch not in pull_branches:
            problems.append(f"The builder claimed {branch} but opened no pull request.")

    parsed, why = parse_result(result)
    if parsed is None:
        problems.insert(0, why or "The builder's structured result is unusable.")
        return problems
    problems.extend(_check_action(before, after, parsed))
    return problems


# ---------------------------------------------------------------------------------------------
# State snapshots
# ---------------------------------------------------------------------------------------------


def build_snapshot(pull_rows: Sequence[Mapping], ref_rows: Sequence[Mapping]) -> dict:
    """Shape raw `gh` output into the state that `check()` compares.

    `pull_rows` come from `gh pr list --json number,title,headRefName,headRefOid,isDraft,labels`;
    `ref_rows` are `{"ref": "refs/heads/build/...", "sha": "..."}`.
    """
    pulls = []
    for row in pull_rows:
        if not TITLE_RE.match(row.get("title", "")):
            continue
        labels = sorted(
            (label["name"] if isinstance(label, Mapping) else str(label))
            for label in row.get("labels") or []
        )
        pulls.append(
            {
                "number": row["number"],
                "title": row["title"],
                "branch": row["headRefName"],
                "head": row["headRefOid"],
                "draft": bool(row["isDraft"]),
                "labels": labels,
            }
        )
    pulls.sort(key=lambda p: p["number"])
    prefix = f"refs/heads/{BRANCH_PREFIX}"
    branches = {
        row["ref"][len("refs/heads/") :]: row["sha"]
        for row in ref_rows
        if row["ref"].startswith(prefix)
    }
    return {"pulls": pulls, "branches": dict(sorted(branches.items()))}


def _decode_stream(text: str) -> list:
    """Decode what `gh api --paginate --jq '.[]'` prints: one JSON value after another."""
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


# ---------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Runner = run_gh,
) -> int:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--snapshot", metavar="FILE", help="write the current state to FILE")
    parser.add_argument("--before", metavar="FILE", help="state saved by --snapshot")
    parser.add_argument("--result-env", metavar="NAME", help="env var holding the result JSON")
    args = parser.parse_args(argv)
    repo = env.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("GITHUB_REPOSITORY is not set.", file=sys.stderr)
        return 1

    if args.snapshot:
        try:
            state = read_state(repo, runner)
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as err:
            print(f"Could not read the repository state: {err}", file=sys.stderr)
            return 1
        with open(args.snapshot, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
        print(
            f"Recorded {len(state['pulls'])} open build pull request(s) and "
            f"{len(state['branches'])} build branch(es)."
        )
        return 0

    if not args.before or not args.result_env:
        print("Give either --snapshot, or both --before and --result-env.", file=sys.stderr)
        return 1
    try:
        with open(args.before, encoding="utf-8") as handle:
            before = json.load(handle)
        after = read_state(repo, runner)
        after["closed"] = closed_since(before, after, repo, runner)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as err:
        print(f"Could not compare the builder result with the repository: {err}", file=sys.stderr)
        return 1
    problems = check(before, after, env.get(args.result_env))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print("The builder's result matches the repository.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
