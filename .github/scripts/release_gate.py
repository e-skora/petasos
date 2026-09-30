"""The release gate: is this exact commit's CI green?

Used by `.github/workflows/deploy-api.yml` before anything deploys (change 005, spec 5.9,
answering the reviewer's finding 8 of 2026-09-30). The workflow fetches two JSON documents
with `gh api --paginate --slurp` and this script decides; the script itself reads no network
and no environment variable, so `tests/test_release_gate.py` drives it with hand-built data.

Two commands:

    release_gate.py select-run --sha <40 hex> --workflow CI runs.json
        `runs.json` is the paginated response of
        GET /repos/{owner}/{repo}/actions/workflows/ci.yml/runs?head_sha=<sha>&per_page=100
        (a list of pages, each with `workflow_runs`). Prints the id of the newest completed
        run of the named workflow at that exact commit and exits 0 only if its conclusion is
        `success`. Any other state (no run, newest run queued or in progress, newest run not
        successful, a run from another workflow) exits 1 with the reason.

    release_gate.py check-jobs --require python,demo,private-identifiers jobs.json
        `jobs.json` is the paginated response of
        GET /repos/{owner}/{repo}/actions/runs/{id}/jobs?per_page=100
        (a list of pages, each with `jobs`). Exits 0 only if every required job name has a
        job in that one run that is `completed` with conclusion `success`. A missing name,
        a non-success conclusion, or a name that appears twice with different conclusions
        exits 1 with the reason.

    release_gate.py smoke-result <code>
        Maps the smoke journey's exit code (scripts/smoke_journey.py, spec 5.10) to the
        release outcome: prints one line for the run summary and exits 0 when the run may
        continue (0 confirmed; 3 and 4 unconfirmed, with a workflow warning) or 1 when the
        release failed (1, 2, or anything else).

Newest means the highest `run_number` (GitHub increases it per workflow); ties are broken by
the highest `id`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _pages(document: Any, key: str) -> list[dict[str, Any]]:
    """Flatten a slurped, paginated response into the list under `key`."""
    if isinstance(document, dict):
        document = [document]
    if not isinstance(document, list):
        raise TypeError("expected a list of pages")
    items: list[dict[str, Any]] = []
    for page in document:
        if not isinstance(page, dict) or not isinstance(page.get(key), list):
            raise TypeError(f"every page must be an object holding a `{key}` list")
        items.extend(item for item in page[key] if isinstance(item, dict))
    return items


def select_run(document: Any, *, sha: str, workflow: str) -> tuple[int | None, str]:
    """Return (run id, reason). The id is set only when the newest run is a success."""
    if not SHA_RE.match(sha):
        return None, "sha must be 40 lowercase hex characters"
    runs = [
        r
        for r in _pages(document, "workflow_runs")
        if r.get("head_sha") == sha and r.get("name") == workflow
    ]
    if not runs:
        return None, f"no `{workflow}` workflow run exists for {sha}"
    newest = max(runs, key=lambda r: (int(r.get("run_number") or 0), int(r.get("id") or 0)))
    if newest.get("status") != "completed":
        return None, f"the newest `{workflow}` run ({newest.get('id')}) is {newest.get('status')}"
    if newest.get("conclusion") != "success":
        return None, (
            f"the newest `{workflow}` run ({newest.get('id')}) concluded {newest.get('conclusion')}"
        )
    return int(newest["id"]), f"run {newest['id']} succeeded"


def check_jobs(document: Any, *, required: list[str]) -> tuple[bool, str]:
    """Every required job name must be completed and successful in this one run."""
    jobs = _pages(document, "jobs")
    by_name: dict[str, set[tuple[str, str]]] = {}
    for job in jobs:
        name = str(job.get("name"))
        by_name.setdefault(name, set()).add((str(job.get("status")), str(job.get("conclusion"))))
    problems: list[str] = []
    for name in required:
        states = by_name.get(name)
        if not states:
            problems.append(f"job `{name}` is missing from the run")
        elif states != {("completed", "success")}:
            problems.append(f"job `{name}` is {sorted(states)}, not completed with success")
    if problems:
        return False, "; ".join(problems)
    return True, "all required jobs succeeded"


SMOKE_OUTCOMES: dict[int, tuple[str, bool]] = {
    0: ("confirmed", False),
    3: ("health ok; demo at capacity, journey not run; release unconfirmed", True),
    4: (
        "journey ran; refund receipt not readable until 004 is deployed; release unconfirmed",
        True,
    ),
}


def smoke_result(code: int) -> tuple[str, bool, bool]:
    """Return (summary line, warn, failed) for a smoke journey exit code."""
    if code in SMOKE_OUTCOMES:
        line, warn = SMOKE_OUTCOMES[code]
        return line, warn, False
    return f"FAILED (exit {code})", False, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_gate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("select-run")
    s.add_argument("--sha", required=True)
    s.add_argument("--workflow", default="CI")
    s.add_argument("runs")
    c = sub.add_parser("check-jobs")
    c.add_argument("--require", required=True)
    c.add_argument("jobs")
    r = sub.add_parser("smoke-result")
    r.add_argument("code", type=int)
    args = parser.parse_args(argv)
    if args.command == "smoke-result":
        line, warn, failed = smoke_result(args.code)
        print(line)
        if warn:
            print(f"::warning::{line}")
        return 1 if failed else 0
    try:
        if args.command == "select-run":
            document = json.loads(Path(args.runs).read_text())
            run_id, reason = select_run(document, sha=args.sha, workflow=args.workflow)
            print(reason, file=sys.stderr)
            if run_id is None:
                return 1
            print(run_id)
            return 0
        document = json.loads(Path(args.jobs).read_text())
        required = [n for n in args.require.split(",") if n]
        ok, reason = check_jobs(document, required=required)
        print(reason, file=sys.stderr)
        return 0 if ok else 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"release gate could not read its input: {exc.__class__.__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
