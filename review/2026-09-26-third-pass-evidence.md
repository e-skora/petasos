# Third-pass review: offline evidence

This accompanies `2026-09-26-third-pass.md`. All probes ran against
`be44eb06328d82b8db81255259351ec5ccc19155`, before switching to the review branch.
They do not test an implementation of change 001.

## Reproduce

In a disposable checkout of the exact reviewed commit, run `uv sync --locked`.
Save the Python block below outside the checkout and run it from the checkout with
`.venv/bin/python /path/to/probes.py`. It imports that commit's existing test fixtures.
Every assertion pins an observed result, including the unsafe results labeled `UNSAFE`.
After correction, those unsafe expectations must be inverted in regression tests.

The validator probes execute the actual Python body extracted from the Claude review
workflow, with synthetic model outputs. They do not run a model or a GitHub workflow.
The collection and merge probes use fake API responses. No probe uses the network.

## Probe code

```python
import dataclasses
import difflib
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

root = Path.cwd()
spec = importlib.util.spec_from_file_location("gate_tests", root / "tests/test_merge_gate.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
g = t.merge_gate


def check(label, facts, expected):
    verdict = g.evaluate(facts)
    print(f"{label}: ready={verdict.ready}; reasons={verdict.reasons}")
    assert verdict.ready is expected


check("positive control", t.ready_facts(), True)
check(
    "old Codex review, no head-bound evidence",
    t.ready_facts(
        reviews=[g.Review("chatgpt-codex-connector[bot]", "COMMENTED", "Old review", t.OLD_SHA)]
    ),
    False,
)
check(
    "Claude comments cannot replace reviewer run",
    t.ready_facts(
        claude_review=None,
        inline_comments=[
            g.Comment(
                "claude[bot]", f"Petasos review: claude\nCommit: {t.SHA}\nBlockers: 0", t.SHA
            ),
            g.Comment(
                "claude[bot]", f"Petasos review: claude\nCommit: {t.SHA}\nBlockers: 1", t.SHA
            ),
        ],
    ),
    False,
)
for state in ("skipped", "failure", "cancelled", ""):
    check(
        "Claude review " + repr(state),
        t.ready_facts(
            claude_review=dataclasses.replace(t.ready_facts().claude_review, jobs={"review": state})
        ),
        False,
    )
check(
    "foreign CI workflow",
    t.ready_facts(ci=dataclasses.replace(t.ready_facts().ci, path=".github/workflows/other.yml")),
    False,
)
check(
    "ordinary question rewrite",
    t.ready_facts(
        files=t.default_files()
        + [
            g.FileChange(
                g.QUESTIONS_PATH, "modified", patch="@@ -1 +1 @@\n-old question\n+replacement\n"
            )
        ]
    ),
    False,
)
check(
    "question deletion",
    t.ready_facts(files=t.default_files() + [g.FileChange(g.QUESTIONS_PATH, "removed")]),
    False,
)
check(
    "plan-specific prohibition",
    t.ready_facts(
        plan_text=t.PLAN_TEXT.replace(
            "wall_forbidden:", "wall_forbidden: `src/petasos/trust/grants.py`"
        )
    ),
    False,
)
check(
    "unratified proposal",
    t.ready_facts(proposal_text=t.PROPOSAL_TEXT.replace("ratified", "proposed")),
    False,
)
check("missing dependency", t.ready_facts(merged_changes=frozenset()), False)
check("invalid grounding", t.ready_facts(grounded_in_main=False), False)
check("incomplete file list", t.ready_facts(files_complete=False), False)


class Collected(t.FakeGitHubForGather):
    def __init__(self):
        super().__init__()
        self.comments = []
        self.raw_files = [dict(filename="src/petasos/trust/grants.py", status="modified")]
        self.review_objects = [
            dict(
                user={"login": "chatgpt-codex-connector[bot]"},
                state="COMMENTED",
                body="Clean",
                commit_id=t.SHA,
            )
        ]

    def request(self, method, path, body=None):
        if path == "/pulls/1":
            return {"changed_files": len(self.raw_files)}
        raise AssertionError((method, path))

    def paged(self, path, key=None, limit_pages=30):
        if path == "/pulls/1/files":
            return self.raw_files
        if path == "/issues/1/comments":
            return self.comments
        if path == "/pulls/1/reviews":
            return self.review_objects
        if path == "/issues/comments/91/reactions":
            return [dict(user={"login": "chatgpt-codex-connector[bot]"}, content="+1")]
        if path == "/pulls/1/comments":
            return []
        raise AssertionError(path)

    def latest_run(self, path, sha):
        return t.ready_facts().ci if path == g.CI_WORKFLOW else t.ready_facts().claude_review


pr = t.open_pr_dict()
pr["body"] = t.BODY_TEXT
api = Collected()
check(
    "collected positive control", g.gather(api, pr, t.MAIN_SHA, frozenset({"000-bootstrap"})), True
)
assert ("changes/001-trust-core/proposal.md", t.MAIN_SHA) in api.file_at_calls
assert ("changes/001-trust-core/plan.md", t.MAIN_SHA) in api.file_at_calls
api.raw_files = [
    dict(
        filename="src/petasos/trust/copied_review.md",
        status="renamed",
        previous_filename="review/trusted-review.md",
    )
]
check(
    "collected protected-source rename",
    g.gather(api, pr, t.MAIN_SHA, frozenset({"000-bootstrap"})),
    False,
)
api.raw_files = [dict(filename="src/petasos/trust/grants.py", status="modified")]
api.review_objects = []
api.comments = [dict(id=91, user={"login": g.GATE_LOGIN}, body=g.codex_request_body(t.OLD_SHA))]
check("reaction on old request", g.gather(api, pr, t.MAIN_SHA, frozenset({"000-bootstrap"})), False)
api.comments[0]["body"] = g.codex_request_body(t.SHA)
check(
    "reaction on current request", g.gather(api, pr, t.MAIN_SHA, frozenset({"000-bootstrap"})), True
)

for result in ({"merged": False}, None, {}, {"merged": True}, {"merged": True, "sha": t.MERGE_SHA}):
    fake = t.FakeGitHubForMain(result)
    with patch.dict(os.environ, {"MERGE_GATE_DRY_RUN": ""}):
        lines = t.run_main(fake, t.ready_facts())
    cleanup = any(c[0] == "DELETE" for c in fake.calls)
    assert cleanup is (
        isinstance(result, dict) and result.get("merged") is True and bool(result.get("sha"))
    )
    print(f"merge result {result}: cleanup={cleanup}")


def make_patch(before, after):
    # GitHub file.patch consists of hunks, not the two file-header lines.
    return "".join(
        list(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/file",
                tofile="b/file",
            )
        )[2:]
    )


injected_patch = make_patch("- [ ] Write x\n", "- [x] Write x\n++ New unapproved instruction\n")
files = [f for f in t.default_files() if f.path != t.TASKS_PATH] + [
    g.FileChange(t.TASKS_PATH, "modified", patch=injected_patch)
]
print("Actual task hunk:", repr(injected_patch))
check("UNSAFE: extra task text hidden as diff header", t.ready_facts(files=files), True)
question_patch = make_patch("-- Existing question\nKeep this\n", "Keep this\nNew question\n")
check(
    "UNSAFE: existing question deletion hidden as diff header",
    t.ready_facts(
        files=t.default_files() + [g.FileChange(g.QUESTIONS_PATH, "modified", patch=question_patch)]
    ),
    True,
)

actual_plan = (root / "changes/001-trust-core/plan.md").read_text()
actual_plan = "grounded_at: `1234567`\n" + actual_plan
for moved in (
    "pyproject.toml",
    "uv.lock",
    ".github/workflows/ci.yml",
    "changes/001-trust-core/spec.md",
):
    check(
        "UNSAFE: current-base change to " + moved,
        t.ready_facts(
            plan_text=actual_plan,
            main_changed_since_grounding=[moved],
            main_changed_since_branch=[moved],
        ),
        True,
    )

failed = t.ready_facts(
    claude_review=dataclasses.replace(t.ready_facts().claude_review, jobs={"review": "failure"})
)
print("Repair status after Claude blocker:", g.status_body(g.evaluate(failed), t.SHA))
print("All probes completed offline; no GitHub writes.")

# Execute the workflow's actual validator, without running Claude or any workflow.
import subprocess
import sys

workflow = (root / ".github/workflows/claude-review.yml").read_text()
validator = workflow.split("          python3 - <<'PY'\n", 1)[1].split("          PY", 1)[0]
validator = "\n".join(line[10:] for line in validator.splitlines())
for label, expected_sha, result, expected_ok in (
    (
        "completed first head",
        t.OLD_SHA,
        {"commit": t.OLD_SHA, "completed": True, "blockers": 0, "findings": []},
        True,
    ),
    (
        "retained blocker on second head",
        t.SHA,
        {
            "commit": t.SHA,
            "completed": True,
            "blockers": 1,
            "findings": [
                {"severity": "blocker", "location": "sample.py:1", "summary": "Retained problem"}
            ],
        },
        False,
    ),
    (
        "wrong head",
        t.SHA,
        {"commit": t.OLD_SHA, "completed": True, "blockers": 0, "findings": []},
        False,
    ),
    (
        "not completed",
        t.SHA,
        {"commit": t.SHA, "completed": False, "blockers": 0, "findings": []},
        False,
    ),
    (
        "contradictory count",
        t.SHA,
        {
            "commit": t.SHA,
            "completed": True,
            "blockers": 0,
            "findings": [{"severity": "blocker", "location": "sample.py:1", "summary": "Problem"}],
        },
        False,
    ),
):
    proc = subprocess.run(
        [sys.executable, "-c", validator],
        capture_output=True,
        text=True,
        env={"RESULT": json.dumps(result), "HEAD_SHA": expected_sha},
    )
    ok = proc.returncode == 0
    assert ok is expected_ok, proc.stderr
    print(f"workflow validator, {label}: accepted={ok}")


# Exercise latest_run itself. Foreign check-run records are never an input.
class Runs(g.GitHub):
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append(path)
        if path.startswith("/actions/workflows/"):
            return {
                "workflow_runs": [
                    {
                        "id": 10,
                        "event": "pull_request",
                        "path": g.REVIEW_WORKFLOW,
                        "head_sha": t.SHA,
                        "status": "completed",
                    },
                    {
                        "id": 11,
                        "event": "pull_request",
                        "path": g.REVIEW_WORKFLOW,
                        "head_sha": t.SHA,
                        "status": "completed",
                    },
                    {
                        "id": 12,
                        "event": "workflow_dispatch",
                        "path": g.REVIEW_WORKFLOW,
                        "head_sha": t.SHA,
                        "status": "completed",
                    },
                ]
            }
        assert path == "/actions/runs/11/jobs?per_page=100"
        return {"jobs": [{"name": "review", "conclusion": "failure"}]}


runs = Runs()
evidence = runs.latest_run(g.REVIEW_WORKFLOW, t.SHA)
check(
    "latest failed review overrides older successful run",
    t.ready_facts(claude_review=evidence),
    False,
)
assert not any("/check-runs" in path for path in runs.calls)
```

## Observed output

```text
positive control: ready=True; reasons=()
old Codex review, no head-bound evidence: ready=False; reasons=('no Codex review for the head commit',)
Claude comments cannot replace reviewer run: ready=False; reasons=('no Claude review run for the head commit',)
Claude review 'skipped': ready=False; reasons=('the Claude review of the head commit is `skipped`, not a clean pass',)
Claude review 'failure': ready=False; reasons=('the Claude review of the head commit is `failure`, not a clean pass',)
Claude review 'cancelled': ready=False; reasons=('the Claude review of the head commit is `cancelled`, not a clean pass',)
Claude review '': ready=False; reasons=('the Claude review of the head commit is `missing`, not a clean pass',)
foreign CI workflow: ready=False; reasons=('the CI evidence is not a pull request run of `ci.yml` for the head commit',)
ordinary question rewrite: ready=False; reasons=('`changes/QUESTIONS.md` may only gain lines',)
question deletion: ready=False; reasons=('`changes/QUESTIONS.md` may only gain lines',)
plan-specific prohibition: ready=False; reasons=("`src/petasos/trust/grants.py` is in the plan's `wall_forbidden`",)
unratified proposal: ready=False; reasons=('the proposal on `main` is not `status: ratified`',)
missing dependency: ready=False; reasons=('dependency `000-bootstrap` has no merged `[build]` pull request',)
invalid grounding: ready=False; reasons=("`main` does not contain the plan's `grounded_at` commit",)
incomplete file list: ready=False; reasons=('the list of changed files is incomplete, so the wall cannot be checked',)
collected positive control: ready=True; reasons=()
collected protected-source rename: ready=False; reasons=('`review/trusted-review.md` is standing-forbidden',)
reaction on old request: ready=False; reasons=('no Codex review for the head commit',)
reaction on current request: ready=True; reasons=()
merge result {'merged': False}: cleanup=False
merge result None: cleanup=False
merge result {}: cleanup=False
merge result {'merged': True}: cleanup=False
merge result {'merged': True, 'sha': 'cccccccccccccccccccccccccccccccccccccccc'}: cleanup=True
Actual task hunk: '@@ -1 +1,2 @@\n-- [ ] Write x\n+- [x] Write x\n+++ New unapproved instruction\n'
UNSAFE: extra task text hidden as diff header: ready=True; reasons=()
UNSAFE: existing question deletion hidden as diff header: ready=True; reasons=()
UNSAFE: current-base change to pyproject.toml: ready=True; reasons=()
UNSAFE: current-base change to uv.lock: ready=True; reasons=()
UNSAFE: current-base change to .github/workflows/ci.yml: ready=True; reasons=()
UNSAFE: current-base change to changes/001-trust-core/spec.md: ready=True; reasons=()
Repair status after Claude blocker: <!-- petasos-merge-gate -->
**Merge gate** for head commit `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`: waiting on

- the Claude review of the head commit is `failure`, not a clean pass
All probes completed offline; no GitHub writes.
workflow validator, completed first head: accepted=True
workflow validator, retained blocker on second head: accepted=False
workflow validator, wrong head: accepted=False
workflow validator, not completed: accepted=False
workflow validator, contradictory count: accepted=False
latest failed review overrides older successful run: ready=False; reasons=('the Claude review of the head commit is `failure`, not a clean pass',)

```
