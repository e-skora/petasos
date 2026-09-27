# Petasos: second review pass

Verdict: **ratify after named fixes**. Do not ratify 000 and 001 or hand them to the cloud builder from this version of PR #3. Fix findings 1 to 6, then review the corrected commit. Finding 7 also blocks starting the cloud loop. D-015, D-016, and D-017 remain settled; no product decision needs to return to Elias.

Reviewed: [PR #3](https://github.com/e-skora/petasos/pull/3), base `3d42db3bfa441f7c97a3d118ba23a8e493dd9f62`, head `19ef3d29254ed1dfe77b74add12431f9e3fc1150`, branch `planning/merge-gate-and-trust-core-v2`. File locations below refer to that head. Review date: 2026-09-26, Pacific time.

## What the rewrite fixes

The central design is substantially better. Finding 1 from the first review is addressed at specification level: server-owned tool definitions supply risk, and approval covers the stored action record, including its amount, destination, arguments, and resource version. Finding 2 is also addressed: a SQLite write transaction serializes execution, and the effect, grant transition, and ledger row must commit together. A retry returns the recorded outcome. Tests 19 to 27 cover the important concurrency and rollback cases.

Most of earlier finding 11 is addressed: tokens are unique across verbs, approval does not charge the rail twice, abort requires an approver, hard constraints are listed, and all 17 valid risk profiles have expected results. Finding 13 is addressed in the contract: retained ledger rows exclude visitor text, and chain verification makes an appropriately limited claim. These are reviewed specifications, not a tested trust-core implementation. Findings 9 and 10 below identify remaining integration and rate-limit details.

## Findings, ranked

### 1. blocker | `.github/scripts/merge_gate.py:196` | Claude's identity does not prove that the reviewer ran

**VERIFIED (REPRODUCED), with the attack path REASONED.** Both the builder and reviewer use the Claude GitHub App. The builder can post comments through its allowed `gh` commands. The gate accepts any correctly shaped `claude[bot]` comment naming the head commit; it never checks which workflow produced it or whether that workflow completed. A builder-posted summary is indistinguishable from a reviewer-posted summary. A local fixture passes without any reviewer-run evidence. Another passes with two summaries for the same commit, one saying zero blockers and the other saying one, because the code accepts any zero.

**Why it matters:** an accidental or induced builder comment can replace one of the required reviews. Conflicting review evidence also resolves permissively.

**Fix:** accept a structured result tied to the exact successful review workflow run and head commit, through a channel the builder cannot forge. Read review instructions from trusted code. Reject incomplete or contradictory results. Add tests for a builder-authored summary, a failed/skipped review, and conflicting summaries. A bot login alone is not proof of origin.

### 2. blocker | `.github/scripts/merge_gate.py:219` | A late thumbs-up can approve a newer commit

**VERIFIED (REPRODUCED).** The reaction has no commit field. The gate accepts it when its time is later than the current commit's CI start. Reproduction: Codex reviews commit A; commit B starts CI; the review of A finishes and adds a thumbs-up. With only an A review and that late reaction, `evaluate()` returns ready for B. [GitHub's reaction response](https://docs.github.com/en/rest/reactions/reactions#list-reactions-for-an-issue) provides a creation time, not the reviewed commit.

**Fix:** require evidence explicitly bound to the reviewed commit. Remove the timestamp-only fallback. If Codex's clean-review behavior provides no such evidence, keep the PR waiting and use a tested adapter that does. Test the A-review/B-push race and a CI rerun. Do not infer a review's target from time.

### 3. blocker | `.github/workflows/claude-review.yml:60` | The plugin can skip the required second review

**VERIFIED source rule; REASONED live consequence.** The workflow runs on every push, but the installed plugin instructs itself to stop if Claude has already commented on the PR. Its rule is PR-wide, not commit-specific. The first run's gate summary therefore meets the skip condition on the next push. The wrapper can then stall without a new review or treat skipped work as zero findings. Source checked at [plugin commit `7779afb`](https://github.com/anthropics/claude-code/blob/7779afb12e3635f46f56ec823979d68350ae000b/plugins/code-review/commands/code-review.md#L10-L19).

**Fix:** use a review procedure that explicitly reviews every new head commit. Give skipped, failed, and completed reviews distinct results; only a completed review may report zero blockers. Prove two consecutive heads, including a deliberately retained blocker. Pin the procedure whose behavior is being relied on.

### 4. blocker | `.github/scripts/merge_gate.py:362` | The file wall misses what a rename removes

**VERIFIED (REPRODUCED).** `gather()` retains only `filename`, dropping `previous_filename` and file status. A mocked GitHub rename from `review/trusted-review.md` to `src/petasos/trust/copied_review.md` passes: the protected source disappears, while only the permitted destination is checked. Related probes show that `wall_forbidden` in the plan is ignored and `changes/QUESTIONS.md` is permitted without checking its append-only restriction.

**Fix:** check both sides of renames, deleted paths, the plan's forbidden paths, and content restrictions on exception files. Refuse incomplete file listings, including pagination limits. Test renaming a protected file into an allowed directory and deleting or rewriting existing questions. The reviewer's model judgment must not substitute for this mechanical check.

### 5. blocker | `.github/scripts/merge_gate.py:380` | The gate does not check whether the change was authorized to build

**VERIFIED (REPRODUCED).** The gate reads the report and plan, but never the proposal's status, dependency completion, or `grounded_at`. A PR with the expected title, branch, files, checks, and comments can merge even while its proposal remains `proposed`. Those checks exist only in the builder's prompt.

**Fix:** read the ratified proposal and plan from a pinned trusted base commit. Require ratification, completed dependencies, a valid grounding commit, and the required check for intervening changes to the allowed files. Test an unratified change, an unmet dependency, and invalid grounding. This enforces the existing process rather than adding a new approval step.

### 6. blocker for 000 | `changes/000-bootstrap/spec.md:29` | The binding setup check names a missing command

**VERIFIED (REPRODUCED).** After installing the locked dependencies, `uv run --no-sync pip --version` fails because `pip` is absent. The spec, plan, and tasks still require `uv run pip check`; CI correctly uses `uv pip check`. The spec also requires a step order that differs from CI. The builder cannot repair CI because workflows are outside its permitted files.

**Fix:** make every bootstrap check use `uv pip check` and describe CI's actual order. Since this PR already adds `uv.lock`, make the planning PR settle the promised switch to `uv sync --locked`; do not leave that forbidden workflow edit to the bootstrap builder. Confirm a fresh environment follows the corrected instructions.

### 7. blocker for starting the cloud loop | `.github/workflows/claude-builder.yml:51` | Claude authentication is not configured

**VERIFIED live setup failure.** The [latest inspected builder run](https://github.com/e-skora/petasos/actions/runs/36282155669) at base `3d42db3` obtained its GitHub App token, then failed because no supported Claude authentication credential was supplied. The repository-secret name listing during this review contained only `PRIVATE_DENYLIST`. PR #3 references `CLAUDE_CODE_OAUTH_TOKEN` in both Claude workflows but cannot create its value.

**Fix:** complete the existing subscription-authentication setup and prove a harmless builder/reviewer run can start before declaring the cloud handoff operational. This is a setup prerequisite, not a request to reconsider subscriptions or the merge policy. No secret values were read.

### 8. should-fix | `.github/scripts/merge_gate.py:437` | Cleanup assumes the merge succeeded

**VERIFIED (REPRODUCED).** The merge response is discarded. A local API fixture returning `{"merged": false}` still produces a success message and a branch-deletion request.

**Fix:** require an affirmative merge result and record the returned merge commit before deleting the branch or dispatching CI. Missing or negative results must wait. This probe verifies local behavior, not that GitHub currently returns this exact failure shape with a successful HTTP status.

### 9. should-fix before 003 | `changes/001-trust-core/spec.md:36` | Session isolation needs an explicit storage contract

**REASONED.** D-017 requires one session's owner to affect only that session. Spec 001 supplies a set of approvers, unrestricted pending-grant views, and `abort_all` over every live grant in the shared database. Separate Gate instances with different approver sets do not by themselves filter stored rows. Spec 003 has not yet supplied the missing boundary.

**Fix:** specify a trusted session scope for grant storage and every list, approve, abort, and execution lookup, or an equally explicit scoped-store design. Add a two-session test including `abort_all`. Keep global abuse counters separate from session counters. This can be settled before 003, but must not be treated as already solved by 001.

### 10. should-fix | `changes/001-trust-core/spec.md:34` | A staging cap is not an execution-per-hour cap

**REASONED.** Reservations stop counting one hour after staging, while grants live for 24 hours. Two refunds staged in each of several hours can all execute in one later hour. The specified algorithm limits admission, not the number of effects in the execution hour. Also, policy-version rule 1.8 names approval rule 1.12 instead of rail rule 1.13, despite the definition saying rail-table changes require a version bump.

**Fix:** explicitly describe the rail as a staging/admission limit, or specify an additional execution limit without charging approval twice. Add a clock-advance test and correct the policy-version references.

### 11. should-fix | `.github/workflows/claude-builder.yml:60` | The repair instructions do not retrieve the gate's reasons

**VERIFIED command behavior; REASONED unattended impact.** Plain `gh run view <id>` shows jobs and steps, not the text written to `GITHUB_STEP_SUMMARY`. The gate also prints its reasons into logs, but the prompt does not request those logs. The builder's action lacks the additional Actions read permission documented for inspecting runs with its App token. See [the action's configuration](https://github.com/anthropics/claude-code-action/blob/756cc22e19660d20e8cc9496b4f242475a7f7790/docs/configuration.md#additional-permissions-for-cicd-integration).

**Fix:** grant the documented read permission and retrieve the specific gate log or a structured result. Test repair from a known waiting reason rather than relying on the model to discover an alternative command.

### 12. should-fix | `.github/workflows/claude-builder.yml:45` | The Python version input is ignored

**VERIFIED from CI logs.** Both the new builder setup and existing CI pass `python-version` to `astral-sh/setup-uv@v3`. The inspected CI run warns that this input is unsupported. It happened to use the runner's Python 3.12.3, so that passing run does not prove the workflow selects the promised version.

**Fix:** select Python through a supported setup step or explicit uv command. Apply the correction in this planning PR because workflow files are outside the builder's permitted files.

## Evidence and limits

- GitHub checks on head `19ef3d2`: `python`, `demo`, and `private-identifiers` passed. The Claude review job was skipped because this is a planning PR. [CI run](https://github.com/e-skora/petasos/actions/runs/36274136647).
- Independently run locally at that head, using Python 3.13.12: locked dependency installation succeeded; pytest reported **72 passed, 1 skipped**; lint, format, and `uv pip check` passed. The skipped test was the private identifier scan, whose secret was not available locally. These are advisory local results, separate from CI's observed Python 3.12.3 evidence.
- The existing tests exercise the gate's pure decision function but explicitly exclude its GitHub collection and merge code. Additional offline probes exercised those paths with fake responses. Their code and output are in [the companion evidence document](2026-09-26-second-pass-evidence.md). No probe posted comments, merged, deleted a remote branch, or dispatched a workflow.
- Live branch rules require the three named checks, linear history, no force pushes, and no deletion. They do not require reviews or an up-to-date branch. Check sources are not pinned to an integration. The gate also accepts checks by name alone; its evidence channel should be restricted to the intended CI workflow when fixing finding 1.
- The gate correctly runs from `main`, blocks ordinary edits under `.github/`, and sends the evaluated head SHA with the merge request. That protects against a different PR head being merged after a push. It does not prove that an advancing base was tested: pin the base used for evaluation and enforce current-base testing where changes could invalidate earlier results.
- Base `main` at `3d42db3` has historical CI failures for formatting and a missing denylist, plus the builder-authentication failure above. Those are not the passing PR-head results. The private identifier check now passes on the PR head.

## Where I am guessing

No trust-core implementation exists yet, so atomic execution and action immutability are design conclusions awaiting the prescribed tests. I did not run a paid Claude review, exercise Codex's live clean-review behavior, or test a real merge. The plugin skip consequence is inferred from the pinned source and wrapper instructions. Forged-summary and rename cases were local fixtures, not attacks against this repository. Current secrets and rules can change after this inspection.

For close-out, preserve the plan's dependency order: ground 000 at its approved planning state, then ground 001 only after 000 has merged. The handoff's suggestion to give both the planning merge commit conflicts with `changes/001-trust-core/plan.md:3`. Record already-settled answers to obsolete questions so the builder does not reopen them. None of this changes Elias's ratified decisions.
