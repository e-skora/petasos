# Petasos: third review pass

Verdict: **remaining blockers. Do not ratify 000 and 001 from this head.** Fix findings 1 and 2 below on PR #3, then review that correction. D-015, D-016, and D-017 remain settled. No product decision needs to return to Elias.

Reviewed: [PR #3](https://github.com/e-skora/petasos/pull/3), base `3d42db3bfa441f7c97a3d118ba23a8e493dd9f62`, head `be44eb06328d82b8db81255259351ec5ccc19155`, branch `planning/merge-gate-and-trust-core-v2`. This pass covers only the correction from `19ef3d29254ed1dfe77b74add12431f9e3fc1150`, the second-pass findings, and problems introduced by those fixes. Locations refer to `be44eb0`. Date: 2026-09-26, Pacific time.

## Findings, ranked

### 1. blocker | `.github/scripts/merge_gate.py:232` | The new file checker can miss added instructions and deleted questions

**VERIFIED (REPRODUCED).** `patch_lines()` discards every line beginning with `+++` or `---`, treating it as a file heading. Inside a change block, those prefixes can instead mean an added line beginning with `++`, or a removed line beginning with `--`.

An offline probe ticks a task and also adds `++ New unapproved instruction`. The gate returns `ready=True`, although the tasks exception permits only ticking existing boxes. Another probe removes an existing question beginning with `--`, adds a new question, and also passes. These are real change blocks generated from before-and-after text, not missing fields or type errors.

**Fix:** compare the complete trusted-base and proposed file contents. Tasks must preserve all text and order except permitted checkbox changes; questions must preserve the old content and append at the end. Alternatively, use a parser that distinguishes file headings from content inside change blocks and rejects incomplete input. Add regression tests for both reproductions. The ordinary rename and question-rewrite probes are fixed, but second-pass finding 4 is not fully closed.

### 2. blocker | `.github/scripts/merge_gate.py:349` | Changes to the build's inputs can leave old checks accepted

**VERIFIED (REPRODUCED); the resulting build failure is REASONED.** The new freshness check looks only at files the PR changes or is allowed to change. Using change 001's actual allowed-path list, a probe reports that main changed `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, or `changes/001-trust-core/spec.md` after the branch split. Each case still returns `ready=True` with the old successful checks and reviews.

These files are outside the builder's editing permission, but they determine its dependencies, tests, and requirements. A planning change can therefore alter what must pass without invalidating evidence from the earlier version. The live main rules also have `strict_required_status_checks_policy: false`, so GitHub does not require the branch to be current before merging. The merge request supplies only the head commit, not a condition on the evaluated main commit. [GitHub merge API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request).

**Fix:** require successful evidence against the current main commit, and protect that requirement through the merge. A simple approach is to update the branch to current main, obtain new checks and reviews, and enforce GitHub's up-to-date check requirement. A selective approach must also track dependency files, workflow files, and governing specifications, and refuse unknown changes. Test a dependency or specification change outside the allowed editing paths and a main update during evaluation. The authorization checks from second-pass finding 5 work, but the accompanying current-base evidence gap remains.

### 3. should-fix | `.github/scripts/merge_gate.py:374` | The repair comment loses the reason Claude's review failed

**VERIFIED output; REASONED unattended consequence.** A completed Claude review with a blocker makes the review job fail. The gate's status comment then says only that the review is `failure`, not a clean pass. The same message covers authentication failures and other execution failures. The repair instructions at `.github/workflows/claude-builder.yml:71` select blocker findings for repair, but do not tell the builder how to distinguish these cases or retrieve the validator's findings. Those findings are printed in the review job's log.

**Fix:** include the review run link and a distinct result for completed-with-blockers versus incomplete/failed review. Tell the builder how to retrieve the structured findings or the exact review log, and test both a known blocker and an authentication failure. The new status comment and Actions read permission solve the original access problem in finding 11; this missing distinction weakens the repair handoff.

## Disposition of the second-pass findings

| Previous finding | Third-pass result |
|---|---|
| 1. Claude review origin | Closed for the reproduced cases. Comments cannot replace the reviewer run. A newer failed review defeats an older success; a contradictory structured blocker count is refused. |
| 2. Late Codex reaction | The old race is refused. A reaction to an old request is ignored; only the current request or a review naming the current commit qualifies. Live behavior of the new reaction adapter remains unproven. |
| 3. Plugin skips later heads | Source fix verified: the plugin is removed and every head is requested explicitly. The actual validator accepts a clean first-head fixture and rejects a second-head fixture retaining a blocker. Two live model runs were not performed. |
| 4. File restrictions | Partly closed. Protected-source renames, ordinary question edits/deletion, plan prohibitions, and incomplete file lists are refused. New finding 1 remains. |
| 5. Build authorization | Ratification, dependencies, grounding, and pinned-main file reads are verified. New finding 2 covers the remaining current-base evidence gap. |
| 6. Bootstrap commands | Closed. The instructions match CI, use `uv pip check`, and install the committed lock with `uv sync --locked`. |
| 7. Claude authentication | `CLAUDE_CODE_OAUTH_TOKEN` is now listed. Its value and usability were not inspected. The harmless post-merge builder run remains an operational prerequisite, not a document-ratification blocker. |
| 8. Merge cleanup | Closed. False, missing, and incomplete merge confirmations cause no branch deletion or CI dispatch. A positive confirmation with a merge commit permits cleanup. |
| 9. Session isolation | Closed at specification level. Rule 1.26 scopes grant operations, execution, and quotas; test 39 covers two sessions and `abort_all`. Implementation is still future work. |
| 10. Hourly limit | Closed at specification level. Rule 1.13 now calls it an admission limit; test 40 advances the clock; rule 1.8 references the correct policy rules. |
| 11. Repair access | Status comments and Actions read permission are present. New finding 3 addresses the remaining loss of useful failure detail. |
| 12. Python selection | Closed. The [pinned setup action](https://github.com/astral-sh/setup-uv/blob/bec219d24cd3e171d82865faccec33120bb574f4/action.yml) supports the input. CI logs show `UV_PYTHON=3.12` and CPython 3.12.3. |

## Evidence

The [response comment](https://github.com/e-skora/petasos/pull/3#issuecomment-5851514939) was checked against the actual correction. Independent offline probes and their complete output are in [the companion evidence document](2026-09-26-third-pass-evidence.md). None contacted GitHub or performed a remote merge, deletion, comment, or workflow dispatch.

At reviewed head `be44eb0`, the independently executed local suite reported **122 passed, 1 skipped** on Python 3.13.12. Locked environment synchronization, lint, format, and `uv pip check` passed. The private-identifier test skipped locally because its secret was unavailable.

[CI run 36284636042](https://github.com/e-skora/petasos/actions/runs/36284636042), also at `be44eb0`, passed all three required jobs: Python reported **122 passed, 1 skipped** on Python 3.12.3; the separate private-identifier job reported **1 passed**; the demo job passed while skipping app steps because the app does not exist yet. Claude review was skipped because this is a planning PR. These results do not establish a working cloud review loop.

## Where I am guessing

The two blockers reproduce permissive gate decisions with local fixtures; no real unsafe merge was attempted. The dependency-change probe establishes acceptance of stale evidence, not an observed production failure. The repair-stall consequence is inferred from the prompt and status output. I did not run Claude, prove the Codex reaction adapter live, or test the future trust-core implementation. Secret names and branch rules can change after inspection.

Once the blockers are corrected and reviewed, preserve the agreed close-out order: ground 000 at the approved planning merge, then ground 001 only after 000 has merged.
