# Petasos: fourth review pass

Verdict: **ready to ratify 000 and 001, with one nonblocking should-fix.** The two
third-pass blockers are closed. Finding 3 is partly closed: review links and repair
instructions work, but the new classification can still mistake an incomplete review
for a completed review with a finding. No new blocker was found in this correction.
D-015, D-016, and D-017 remain settled.

Reviewed: [PR #3](https://github.com/e-skora/petasos/pull/3), base
`3d42db3bfa441f7c97a3d118ba23a8e493dd9f62`, head
`de7ebe95cb2e432ecbd3cf7e6219f2a0bac59157`, branch
`planning/merge-gate-and-trust-core-v2`. Scope: only the correction from
`be44eb06328d82b8db81255259351ec5ccc19155`, closure of the three third-pass
findings, and problems introduced by those fixes. Locations below refer to `de7ebe9`.
Date: 2026-09-26, Pacific time.

## Findings, ranked

### 1. should-fix | `.github/scripts/merge_gate.py:304` | An incomplete review can enter the builder's repair queue

**VERIFIED (REPRODUCED) with offline fixtures; the wasted repair attempt is REASONED.**
The classifier assumes that a successful model step followed by a failed result check
means the review completed and found a problem. A successful action step does not
prove that its returned review says `completed: true`.

The workflow's actual validator rejects a schema-valid result with the correct commit,
`completed: false`, zero blockers, and no findings. Its error says
`Review not clean: the review did not complete`. Given a successful model step and that
failed validation step, the new gate nevertheless says
`the Claude review of the head commit completed and found a problem`.
The same classification occurs for a result naming the wrong commit or for missing
structured output. These are simulated action conclusions, not observed live model runs.

The new builder instructions at `.github/workflows/claude-builder.yml:71` select this
message for repair after four hours. A builder can therefore spend a repair attempt on
a PR that has no valid finding to fix. The gate still refuses the merge in every tested
case, so this remains a should-fix rather than a blocker.

**Fix:** preserve a machine-readable result that separates a valid, completed review of
the requested commit with blockers from an incomplete or invalid result. Let the gate
read that trusted result before declaring that the review found a problem. Alternatively,
make the status neutral about why validation failed and require the builder to verify
completion and the commit in the linked log before selecting a code repair. Keep the
run link and log command. Add cases for `completed: false`, the wrong commit, missing
output, a valid blocker, and a failed model step. Invalid review output must not consume
a code-repair attempt.

## Disposition of the third-pass findings

| Previous finding | Fourth-pass result |
|---|---|
| 1. File comparison misses added instructions and deleted questions | **Closed, VERIFIED.** Both original before-and-after probes are refused. The collection path reads the task and question files at the pinned main and head commits. Added task text, replacement questions, and unreadable head content are refused; valid ticks and appends pass. |
| 2. Old evidence survives changes to build inputs | **Closed, VERIFIED code and live configuration.** All four earlier dependency, workflow, and specification cases are refused with `behind_main=True`. Collection marks an older common ancestor as behind main, accepts current main, and refuses an unknown comparison. The live main ruleset requires an up-to-date branch. The final merge-time enforcement was not exercised with a real merge. |
| 3. Repair comment loses why Claude failed | **Partly closed.** The run link, step details, and log-reading instructions are present. A completed blocker and a failed model step produce distinct messages. The should-fix above covers incomplete or invalid output from a successful model step. |

## Evidence

The [response to the third-pass review](https://github.com/e-skora/petasos/pull/3#issuecomment-5851893733)
was checked against the actual six-file correction. The complete adapted probes,
additional boundary probes, and observed output are in
[the companion evidence document](2026-09-26-fourth-pass-evidence.md).
All probes ran offline at the exact reviewed head. No probe contacted GitHub or
performed a remote merge, deletion, comment, or workflow dispatch.

The prior probes were adapted to use whole `before` and `after` contents and
`behind_main`. All six formerly permissive cases now refuse: two file-content cases
and four stale-base cases. Positive controls still pass. The earlier review-origin,
old-request reaction, protected-source rename, authorization, incomplete file list,
merge-cleanup, and workflow-validator probes retain their restrictive outcomes.

Independently executed locally at `de7ebe9`, on Python 3.13.12:

- `uv sync --locked --offline`: passed against the existing environment.
- `pytest -q`: **153 passed, 1 skipped**. The private-identifier test skipped because
  its secret was unavailable locally.
- Ruff lint, Ruff format check, and `uv pip check`: passed.

Independently observed [CI run 36287907887](https://github.com/e-skora/petasos/actions/runs/36287907887)
at the same full head commit: all three required jobs passed. The Python job used
Python 3.12.3 and reported **153 passed, 1 skipped**; the separate private-identifier
job reported **1 passed**. The demo job passed while skipping its install, test, and
build steps because the app does not exist yet. The Claude review job was skipped
for this planning PR. These checks do not prove the live autonomous review loop.

The live [main ruleset 24053264](https://github.com/e-skora/petasos/rules/24053264)
was read through the GitHub API: enforcement `active`, default-branch target,
`strict_required_status_checks_policy: true`, required checks `python`, `demo`, and
`private-identifiers`, and no bypass actors. GitHub documents that this strict setting
requires the branch to be current with the base before merging.
[GitHub rules documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-status-checks-to-pass-before-merging).

At inspection, main was still `3d42db3`; its latest CI run,
[36272188891](https://github.com/e-skora/petasos/actions/runs/36272188891), had failed.
This is separate from the passing checks on the proposed planning head. The review
files are authored on `review/fourth-pass-2026-09-26` from that main commit; no product,
implementation, workflow, or decision file is changed by this review.

Publication note: [review PR #6](https://github.com/e-skora/petasos/pull/6) uses the
older main files. Its first CI run also flagged formatting in the new evidence examples;
those examples were then formatted, and both review documents pass the local format
check. The remaining local format failures are the three unchanged files already
reported by main CI: the 000 and 001 specifications and the private-identifier test.
The PR's old Claude review workflow also failed to install its code-review plugin,
before reviewing anything. These publication-check failures are separate from the
passing checks on the reviewed planning head. The review branch does not change those
out-of-scope files.

## Where I am guessing

The remaining finding reproduces the validator output and the gate message using
synthetic action results. I did not run Claude to establish how often incomplete output
occurs, or run the builder to observe a wasted repair attempt. I did not attempt a real
merge while main changed; the race protection conclusion relies on the verified ruleset
and GitHub's documented behavior. Settings can change after inspection.

The live Codex reaction behavior, Claude structured output on an actual builder PR,
and builder push permissions remain unproven operational checks. The future trust-core
implementation is outside this pass. The new automated regression tests were read and
executed, but their results alone are not treated as independent proof; the separate
probes establish the closure claims above.

## Next action

Proceed with the agreed planning close-out and ratification of 000 and 001. Keep the
remaining should-fix recorded for a bounded correction; it does not block ratification.
Ground 000 at the approved planning merge. Ground 001 only after 000 has merged.
This is a review recommendation, not an edit to either proposal's status. No merge,
ratification edit, release, or deployment was performed in this review.
