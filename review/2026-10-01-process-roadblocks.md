# Process roadblocks: adopt with named changes

Review date: 2026-10-01. Repository: `e-skora/petasos`.
Reviewed base: `32a151d289638ff4377d8273efeac4797531ec9a`.
Review branch: `review/process-roadblocks`.

## Verdict

**Adopt with named changes.** Keep the proposal's central rule: every waiting state needs an owner, a next action, and a deadline. Replace the proposed comment-driven dispatch loop with a small deterministic controller, meaning code that decides from recorded facts rather than a model interpreting prose. Fix the current gate's stale-state and parsing defects first. Do not enable planning auto-merge as described in C9.

The review is process-only. It does not reopen the implementations or specifications for changes 000 to 007. It proposes changes for Elias to rule on; it authorizes no workflow edit, settings change, merge, or release.

Evidence labels used below:

- **VERIFIED** means an independently executed local reproduction, a live GitHub API observation, or a statement established by the linked current official documentation/source. The text distinguishes those forms of evidence.
- **REASONED** means an inference, a proposed design, or a supplied historical explanation that the available evidence does not independently establish.
- **CONFIRMED**, **PARTLY**, and **WRONG** judge the handoff's claims, not whether a proposal has approval.

## Evidence and corrected timing

**VERIFIED, API observation:** the supplied base was still `main` at the 23:03 UTC status check. [CI run 36936694929](https://github.com/e-skora/petasos/actions/runs/36936694929) passed on that exact commit. [Builder run 36936701077](https://github.com/e-skora/petasos/actions/runs/36936701077), manually dispatched at 22:42:49 UTC, was still running. A branch for 004 existed; the open-PR API returned no PRs during inspection. Thus “004 and 005 are both in flight” is not an observed fact. The workflow permits overlapping open builds across separate runs, although its builder jobs run one at a time.

**VERIFIED, API timestamps:** PR open time, measured from creation to merge, was:

| Build | Pull request | Open time |
|---|---|---:|
| 000 | [#10](https://github.com/e-skora/petasos/pull/10) | 19m 14s |
| 001 | [#14](https://github.com/e-skora/petasos/pull/14) | 7h 57m 03s |
| 002 | [#19](https://github.com/e-skora/petasos/pull/19) | 12h 55m 03s |
| 003 | [#21](https://github.com/e-skora/petasos/pull/21) | 28h 37m 06s |

Total: **49h 48m 26s**. The first value in the handoff, 0.6 hours, is wrong for PR open time. The four initial builder runs lasted approximately 20.4, 27.7, 16.8, and 41.2 minutes, including workflow overhead. All four were `workflow_dispatch` runs. Some later repairs were scheduled runs.

**REASONED:** “48 hours waiting,” split into 16 hours of age checks, 8 hours of missing schedules, and 24 hours of deadlock, is a useful diagnosis but not a measured, mutually exclusive time accounting. The age check and missed schedules overlap, and most initial build time occurred before PR creation. Do not publish that split as independently verified.

## Ranked findings

### 1. Blocker: the gate ignores fresh holds and mixes snapshots

**VERIFIED, REPRODUCED.** At [merge_gate.py:621-683](https://github.com/e-skora/petasos/blob/32a151d289638ff4377d8273efeac4797531ec9a/.github/scripts/merge_gate.py#L621), `gather()` fetches the current PR into `full`, but uses that response only for the changed-file count. It takes the head, title, draft flag, labels, and body from the older list response. `wait_for_codex()` repeatedly passes that same old response. A fake API returning a newly added `hold` produced `labels=[]` and `ready=True`.

The merge request's head SHA protects against a changed head. It does not protect a `hold` added without a commit. Also, the files endpoint is current while report/review reads use the old head, so the evidence need not describe one revision. The handoff misses this.

**Fix, REASONED:** build one snapshot from a fresh PR response; obtain the changed paths for the pinned base/head; restart if the head or base changes. Recheck open state, draft, hold, authorization, and evidence immediately before dispatch or merge. Keep the SHA on the merge request and the strict base rule. A last-second metadata race still needs a defined policy: if `hold` must be absolute, put cancellation and merge admission behind the same controller or a GitHub-enforced requirement. A final GET alone is not an atomic lock.

### 2. Blocker: planning auto-merge would bypass the intended approval boundary

**PARTLY on C9; VERIFIED configuration, REASONED consequence.** [The live main ruleset](https://github.com/e-skora/petasos/rules/24053264) required only `python`, `demo`, and `private-identifiers`, with strict freshness and no bypass actors. It did not require the merge gate, a planning review, or a ratification check. Repository auto-merge was disabled. Merely turning it on makes GitHub merge according to those required checks, which do not enforce D-015's two-review/report rules. A `planning` label or an existing file under `review/` is not evidence that the current proposal was accepted.

There is already a broader trust limit: the builder has write credentials and broad `gh` access; “never merge” is a prompt instruction. The script gate is not the only technically possible merge path for a writer with green required checks.

**Fix, REASONED:** before enabling auto-merge, require a trusted admission check for every PR class. A build must satisfy the existing build policy. A planning PR must bind an accepted review and any required owner ruling to the exact proposal/spec revision and current diff. Reject mixed or unknown classes. Check both sides of renames and full file contents where relevant. Source the check from trusted default-branch code and a protected identity; a PR must not be able to replace or impersonate it. Record the delegation before using it. Moving the merge to GitHub is sound; routing around a permission refusal without establishing that authority is not.

### 3. Blocker: a status comment and cooldown are not sufficient dispatch controls

**PARTLY on C2/C3/C6; VERIFIED current behavior, REASONED design risk.** [The builder prompt:66-96](https://github.com/e-skora/petasos/blob/32a151d289638ff4377d8273efeac4797531ec9a/.github/workflows/claude-builder.yml#L66) locates a comment by marker and asks the model to interpret it. It does not specify an author check. Even `github-actions[bot]` is shared by repository workflows, not a unique gate identity. A new machine line would therefore be an unsafe authority source. A cooldown also permits endless repeated dispatches after it expires and loses work when a wake-up is suppressed while a builder is busy.

**Fix, REASONED:** the controller must calculate eligibility itself and store an action record keyed by repository, PR/change, base SHA, head SHA, action type, and attempt. Claim the record atomically before dispatch, record the resulting run, and give the claim an expiry and recovery path. Duplicate events must reuse that record. A request timeout may mean dispatch succeeded, so reconcile before retrying. Pass a bounded action identifier, not PR prose, to a fixed workflow on `main`. The builder revalidates the record before acting. Treat comments as a display only.

Block draft, hold, fork, unratified, missing-dependency, invalid-wall, and stale-grounding cases before spending a model call. Validate the triggering run's repository/workflow and then fetch current facts; an event is a wake-up, not permission. Never interpolate a title, comment, or branch supplied by a PR into shell code. Keep privileged controller jobs from executing PR code or consuming its caches.

### 4. Blocker: the wall parser can fail open

**WRONG on B8's “both fail closed”; VERIFIED, REPRODUCED.** At [merge_gate.py:243-254](https://github.com/e-skora/petasos/blob/32a151d289638ff4377d8273efeac4797531ec9a/.github/scripts/merge_gate.py#L243), a blank line immediately after `wall_forbidden:` ends parsing with an empty list. With a broad expected wall and a forbidden file below that blank line, the gate returns ready for that file. The standing-forbidden list still applies, but the plan-specific restriction disappears. No actual 004/005 bypass was observed.

**Fix, REASONED:** validate the complete wall document before ratification and again in the gate. Require a structured expected list and an explicit forbidden list, including an explicit empty value if allowed. Reject malformed, duplicate, truncated, or ambiguous sections. Do not silently accept whatever paths a prose parser happened to recover.

### 5. Should-fix: separate waiting, failed infrastructure, and actionable findings

**CONFIRMED on A5; VERIFIED, REPRODUCED.** [merge_gate.py:423-442](https://github.com/e-skora/petasos/blob/32a151d289638ff4377d8273efeac4797531ec9a/.github/scripts/merge_gate.py#L423) ignores workflow `status`. An in-progress review with no conclusion is described as invalid with a missing job. Those obsolete messages remain on several already-merged PRs because the gate does not replace the waiting comment on success.

**Fix, REASONED:** represent pending, running, completed-clean, completed-with-findings, failed, cancelled, timed-out, missing, and unknown separately. Give failed infrastructure to a bounded retry worker, not the code repairer. A Codex timeout permits retry or escalation, never a clean-review inference. Decide explicitly whether to repair known findings before all reviewers finish; if doing so, retain later findings and reject stale evidence. Four hours is neither proof of completion nor a useful safety boundary.

### 6. Should-fix: the Codex evidence contract needs a real clean-result fixture

**PARTLY on B2/C8; VERIFIED, REPRODUCED where noted.** Current [OpenAI documentation](https://learn.chatgpt.com/docs/third-party/github) describes standard GitHub reviews and an eyes reaction while processing. It does not establish a reliable no-findings payload or a thumbs-up promise. The inspected build PRs had Codex review objects, and the nine gate request comments had no reactions. Ordinary issue comments are not read as findings or completion evidence by this gate.

A second probe found that a sole `DISMISSED` Codex review with an empty body is accepted by [evaluate():445-454](https://github.com/e-skora/petasos/blob/32a151d289638ff4377d8273efeac4797531ec9a/.github/scripts/merge_gate.py#L445). The current request helper also asks Codex on a draft when CI is green. These are not useful definitions of completed review or eligible work.

**Fix, REASONED:** accept only explicitly supported completed evidence states, with trusted identity and exact commit binding; reject dismissed/pending reviews. Define how superseding reviews affect earlier blockers. Capture a clean result before enabling a new comment parser. A comment mentioning a hash is insufficient unless its complete result format is validated. [Reaction objects](https://docs.github.com/en/rest/reactions/reactions#list-reactions-for-an-issue-comment) carry a user, reaction, and time, but no reviewed commit. A reaction attached to a mutable request does not by itself prove what diff was reviewed. Preserve request identity and integrity, and do not broaden the current thumbs-up rule without evidence.

### 7. Should-fix: a watchdog inside the stalled scheduler is not independent

**CONFIRMED on the missing recovery ownership; REASONED fix.** C4 creates visibility only when the gate runs. If Actions scheduling or the gate itself stops, it cannot open its own overdue issue. Assigning an issue also does not wake a closed planning session.

Use an independent heartbeat to check the controller's last successful scan and unresolved deadlines. Its first job can be alerting, not operating on code. Give each issue a reason code, next actor, next check time, and exact evidence. Keep it open until resolved or handed to an acknowledged successor. Notify on meaningful transitions, not every poll. GitHub notification delivery and the planning worker's ability to receive work need an end-to-end check before calling this unattended.

### 8. Should-fix: separate budgets, but do not make syncs unlimited

**PARTLY on A4/C6/C7; VERIFIED configuration, REASONED fix.** The two-repair limit is a model instruction counting commit subjects, not a counter enforced by the gate. Both 002 and 003 reached two such commits, but 002's repairs fixed two findings; it is not evidence of sync-only budget waste. 003's last repair settled a flag and synchronized the base. Merge commits may have their own non-`repair:` subjects.

Track finding repairs, infrastructure retries, and base syncs separately in controller state. Deduplicate syncs by target base; cap repeated conflicts and repeated no-progress attempts. Count branch work, not inherited `repair:` commits from `main`, and do not let a changed subject reset a limit. A sync that needs an out-of-wall conflict resolution belongs to planning.

The action is already pinned and the builder already has `timeout-minutes: 90`. Add a turn limit and structured result validation. Validate observed outcomes too: a claimed new PR must exist, a repair must produce the recorded head, and a no-op must name a valid current reason. A model saying “repaired” is not proof.

## Check of every A and B claim

The table uses the findings above for the principal fixes. Remaining limitations are explicit.

| Claim | Assessment and evidence |
|---|---|
| A1: flag deadlock | **CONFIRMED mechanism; PARTLY history. VERIFIED:** PR #21's report at `85105e16182379c71ce834955ad69fb7be9b8077` says `BUILT WITH FLAGS`; its branch contains the naming question. The gate requires exact `BUILT`. PR #24 merged the answer at 22:09:43 UTC on October 1; `41ce27b3` cleared the flag at 22:12:36, and #21 merged at 22:22:09. Three October 1 scheduled runs ended green without a new build head. **REASONED:** planning's motive for withholding the answer comes from the handoff. Also, the builder had already made the naming choice and opened a non-draft PR, contrary to the stop-and-flag procedure. |
| A2: four-hour proxy | **CONFIRMED, VERIFIED** in builder lines 71-80. Age cannot distinguish a running reviewer from a finished one. The 003 reviews were posted by 18:11 UTC on September 30; the next repair began at 21:46. Exact completion times and non-overlapping delay totals are not fully reconstructed. |
| A3: unreliable cron | **CONFIRMED core, PARTLY attribution. VERIFIED:** schedule is `17 */2 * * *`; all four initial builds used manual dispatch. GitHub run history has multi-hour schedule gaps, and [GitHub documents possible delay/drop](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). The API cannot prove why a particular slot was absent or whether it was dropped rather than delayed. |
| A4: two repairs, sync costs | **PARTLY, VERIFIED:** see finding 8. The prompt combines sync and finding repair. Do not claim both builds exhausted their caps because of syncs. |
| A5: pending reported as failure | **CONFIRMED, VERIFIED/REPRODUCED:** finding 5. |
| A6: local permission blocks | **PARTLY. VERIFIED:** [Anthropic's current permission-mode docs](https://code.claude.com/docs/en/permission-modes#what-the-classifier-blocks-by-default) list unapproved PR merges among default blocks. **REASONED:** the exact Cowork session, classifier reason, and effect of “go please” were supplied, not independently inspected. The Claude Code documentation does not establish that session's connector behavior. Auto-merge commands may still face permission review. |
| A7: repair changed downstream assumptions | **CONFIRMED process evidence, VERIFIED:** commit `458eadb231266241515c734339115958332b4e7a` describes adding ticket activity during repair; PR #25 updates 004's grounding/spec. This review did not repeat the product code or data-safety review. Add contract checks for payload shape and allowed fields; an import-only test would miss this change. |
| A8: token failures | **PARTLY, VERIFIED:** runs [36282155669](https://github.com/e-skora/petasos/actions/runs/36282155669), [36292043998](https://github.com/e-skora/petasos/actions/runs/36292043998), and [36313546765](https://github.com/e-skora/petasos/actions/runs/36313546765) show missing credential configuration, invalid OAuth authentication, and a line break in the token. The same secret is referenced by all three workflows. “Nothing checks it” is too strong: the action detects errors; the missing piece is early health reporting and recovery. The hand-copy cause and 8.5-hour attribution are not fully verified. |
| A9: manual spec relay | **PARTLY. VERIFIED:** AGENTS.md defines a separate reviewer and pre-ratification review; there is no repo workflow that performs that relay. **REASONED:** exact private-chat elapsed time and three-pass effort are handoff history. Moving packets to linked PRs can reduce relay work without combining author and reviewer roles. |
| B1: abandoned states | **PARTLY, VERIFIED:** builder and Claude review skip drafts, but the gate lists them, reports draft as a reason, and can even request Codex. The authorization, review-failure, and late-Codex recovery gaps are real. No explicit alert owner or retry path exists. A `hold` needs acknowledgement and notification, not automatic removal. |
| B2: clean Codex path | **PARTLY, VERIFIED within inspected history:** nine gate requests had no reactions. The no-findings format remains unsettled; see finding 6 and D3. |
| B3: green no-ops | **CONFIRMED local gap, PARTLY vendor claim. VERIFIED:** builder output has no required action record; inspected stalled runs ended success. This does not prove the vendor's early-turn bug caused them. Give intentional no-ops and incomplete execution different outcomes. |
| B4: parallel 004/005 | **PARTLY. VERIFIED:** only 004 was observed running. The expected walls are disjoint, and 005 forbids changes to `app.py`; do not assert an overlapping file collision. **REASONED:** if both branch before either merges, the second must sync. Concurrent question appends can conflict, but not inevitably. Questions have an explicit append exception to the wall. |
| B5: should-fix debt | **PARTLY, VERIFIED:** AGENTS.md requires a disposition, and Claude findings are in public comments/logs, so ownership is not wholly absent or evidence wholly private. No automated durable queue enforces follow-through. Extract unresolved findings with stable IDs, original source/head, owner, and disposition; deduplicate across reviews and repairs. |
| B6: only planning watches | **PARTLY, VERIFIED:** gate/builder scheduling continues without the chat, but there is no independently implemented stall watcher or planning worker. “Nothing watches” is too broad; “nothing owns recovery from these stalls” is supported. |
| B7: delegation undocumented | **PARTLY, VERIFIED:** AGENTS.md reserves status/decision rulings to Elias, but QUESTIONS.md line 17 already quotes the standing delegation for one decision. A general ratification delegation with scope and exclusions is absent from DECISIONS.md. Do not infer a new ruling from that narrower record. |
| B8: parser brittleness | **PARTLY; fail-closed assertion WRONG, VERIFIED/REPRODUCED:** finding 4. Expected-list truncation can reject valid files; forbidden-list truncation can admit prohibited ones. |
| B9: cloud transition | **CONFIRMED announcement, VERIFIED:** [Anthropic's support page](https://support.claude.com/en/articles/15520349-use-claude-cowork-on-web-desktop-and-mobile) announces October 6 for new Pro/Max tasks; existing local tasks stay local. Local files/connectors still require the desktop app. **REASONED:** the exact shell connector's future behavior is not established. Removing a local dependency is sensible, but no observed migration failure is claimed. |

## Better design, including the missed states

The following is **REASONED proposed design**, not an implemented controller.

Retain the existing merge conditions. Add an explicit recovery layer around them. Its action record should contain state, reason code, responsible actor, deadline, base/head, evidence run IDs and attempts, remaining budgets, and the pending action's identifier. A PR comment and workflow summary show that record in plain language.

| State | Next action and owner |
|---|---|
| Eligible ratified work, no PR | Controller claims and starts one build. Scan this queue even when no build PR exists. |
| Builder running | Wait for that recorded run; on completion validate its result and scan again. |
| CI/reviewer pending or running | Wait until the recorded deadline; do not consume a code-repair slot. |
| Review/CI infrastructure failed, absent, cancelled, or invalid | Retry that specific service within its own budget; then notify the operator. Missing credentials require an operator, not a code patch. |
| Completed actionable findings | Repair the exact current head under the file wall and repair budget. |
| Behind main | Sync once for the current base, then collect entirely new evidence. |
| Flag, authorization gap, stale grounding, or forbidden conflict | Planning receives a bounded packet. Elias is called only for a ruling outside recorded delegation. |
| Draft or exhausted budget | Planning explicitly resolves, resumes, or abandons it. A draft is not an invisible terminal state. |
| Held | Preserve the hold; notify its owner. Resume only on authorized release. |
| Ready | Revalidate, merge with expected head, record confirmation, then run post-merge actions. |
| Merged but cleanup/CI/next-build dispatch failed | Retry those post-merge actions independently. Do not rediscover them only by scanning open PRs. |
| Controller itself silent | Independent heartbeat alerts the operator. |

Initial deadline values must be configurable policy, not vendor guarantees. A reasonable starting proposal is 10 minutes to investigate a missing expected run, the existing job timeout plus a short grace period for a running job, and 30 minutes before a Codex retry. Cap Codex requests at three **total** per head, not “one retry, max three” ambiguously. Retain an overall per-change no-progress limit so repeated new heads cannot erase the budget.

Other missed conditions, **VERIFIED source paths with REASONED consequences**:

- [main():749-751](https://github.com/e-skora/petasos/blob/32a151d289638ff4377d8273efeac4797531ec9a/.github/scripts/merge_gate.py#L749) returns when there are no build PRs. Adding only “dispatch after merge” will not start newly ratified work after a planning-only merge. Reconcile on relevant `main` changes and builder completion too.
- A builder completion currently does not wake the gate. A skipped dispatch while it was busy can be lost until cron. Keep a durable outstanding action and reconcile on completion, including failure and timeout.
- [main():763-765,789-790,812-828](https://github.com/e-skora/petasos/blob/32a151d289638ff4377d8273efeac4797531ec9a/.github/scripts/merge_gate.py#L763) logs read, request, merge, cleanup, and dispatch failures yet can exit zero. Distinguish ordinary waiting from controller failure. Confirm failed post-merge dispatches are retried after the PR leaves the open list.
- Three separate ten-minute Codex waits can exceed the gate's 25-minute job limit and starve later PRs. Repeated `gather()` calls also refetch all evidence. Use a total run budget, fair ordering, and narrow polling. [GitHub's default token limit is 1,000 REST requests/hour/repository](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api#primary-rate-limit-for-github_token-in-github-actions); honor rate-limit responses and backoff.
- A marker-copying public comment can suppress the current once-per-head Codex request because existing bodies are checked without author filtering. Fix request provenance as well as status provenance.
- The mention workflow uses no shared builder concurrency group. An authorized `@claude` repair can overlap a scheduled repair. All mutation paths need the same per-PR action claim. Scheduling serialization alone is insufficient.
- Stale-claim cleanup deletes a branch after six hours without establishing that it contains only a claim. Preserve or escalate branches containing work. A build that timed out after coding but before opening a PR needs recovery, not age-based deletion.
- Merely asking a reviewer to return the supplied head does not freeze `gh pr diff`, which reads the live PR. Read the exact commit comparison and verify base/head again before accepting the result. Test run cancellation and reviewer output arriving after a newer head.
- The file wall operates at merge time. It is not a sandbox for tests or review jobs that already touched the PR. Keep secrets and write credentials out of untrusted code execution, and inspect changes to workflows/configuration before privileged jobs use them. A trusted `workflow_run` entry point alone does not solve every credential boundary.

For 004/005, prefer one active build through merge while the recovery loop is being repaired. This is a scheduling proposal, not a new product dependency. If retaining overlapping builds, explicitly own the second branch's sync and question-file conflict handling. Do not spend code-repair attempts on an uncontested base sync, and do not automatically resolve a planning decision. Their disjoint walls do not prove compatibility of shared interfaces or runtime composition.

### Disposition of each C item

| Item | Recommendation |
|---|---|
| C1 | Adopt. Include missing/unknown states and replace stale comments on merge or terminal failure. |
| C2 | Change. Add reason codes, revision, deadline, and action identity. The comment is a view; the controller owns authority. A timeout never satisfies a review requirement. |
| C3 | Change. Use recorded, deduplicated actions, exact eligibility checks, completion wake-ups, and a tested bot-auth path. A busy-run check and cooldown are not enough. |
| C4 | Adopt with an independent heartbeat and real planning delivery. Do not close unresolved issues solely because the actor name changed. |
| C5 | Adopt with stable question IDs and authorized answer references. Verify every remaining flag is settled before changing the verdict. Align QUESTIONS.md's current “delete the entry” instruction with the new procedure. Editing away from the end reduces conflict risk but cannot guarantee no conflict. |
| C6 | Change. Separate and bound sync/fix/infrastructure attempts in controller state. `sync:` is a label, not enforcement. Use expected head on an API branch update and wait for new review/CI evidence. |
| C7 | Adopt the new parts. Structured output is supported; SHA pin and job timeout already exist. Add outcome verification, a turn limit, and an always-running result/health step. |
| C8 | Change. Three total requests per head, explicit quota state, no timeout-as-pass, and a proven evidence format. Reject stale, pending, and dismissed evidence. |
| C9 | Do not adopt as written. Establish trusted admission and exact-review/ratification binding before enabling auto-merge. Do not assume the local permission classifier will allow `--auto`. |
| C10 | Adopt with deduplication and disposition checks. Save all Claude findings as a durable artifact too: the successful zero-blocker step currently prints no should-fix details. Route debt into a reviewed hardening proposal, not an automatically ratified change. |
| C11 | Strengthen. Test signatures, payload schemas, allowed fields, error behavior, and representative consumer interactions. Import tests detect missing names, not the nested-data change that motivated A7. Keep dependencies in the spec/plan; do not run free-form spec text as CI code. |
| C12 | Move earlier. Elias must supply or confirm the precise delegation and exclusions before automatic planning merges or ratifications rely on it. |

Recommended order: stale-state/parser fixes and state reporting; delegation and trusted admission design; bounded controller actions and independent monitoring; then dispatch and separate budgets; then Codex-format validation, planning merge automation, debt tracking, and contract checks. Observe a full cycle in a disposable test repository before activating new write paths here.

## Answers to D

1. **Bot-started Claude builder:** **VERIFIED source, not a live exchange test.** The pinned action's [agent preparation](https://github.com/anthropics/claude-code-action/blob/756cc22e19660d20e8cc9496b4f242475a7f7790/src/modes/agent/index.ts#L29) calls `checkHumanActor`; its [actor validator](https://github.com/anthropics/claude-code-action/blob/756cc22e19660d20e8cc9496b4f242475a7f7790/src/github/validation/actor.ts) rejects bots unless allowed. Petasos's builder sets no `allowed_bots`. Therefore C3 cannot simply dispatch the unchanged builder as `github-actions[bot]`. Allow only that intended bot, after securing dispatch admission, and test it. The action [exchanges the job identity for an app token before this check](https://github.com/anthropics/claude-code-action/blob/756cc22e19660d20e8cc9496b4f242475a7f7790/src/entrypoints/run.ts#L176); whether the hosted exchange accepts this precise new invocation remains **unverified**. Schedule success with a human actor does not prove it.

2. **Three-level chain through dispatch:** **VERIFIED:** [GitHub documents three downstream `workflow_run` levels](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run). **REASONED:** an explicit dispatch and a subsequent app-token push should form fresh event roots, so the proposed loop need not be one growing `workflow_run` chain. The documentation read does not explicitly promise how ancestry across dispatch is counted. Test more than three consecutive cycles before relying on that inference; do not assert the limit resets as a verified fact.

3. **Clean Codex output:** **VERIFIED:** current OpenAI docs describe review objects and a processing reaction; inspected repository evidence proves review objects, not a clean-response format. GitHub reactions contain no commit ID. **Unsettled:** which result this integration emits on a genuinely clean head. A disposable clean PR and captured API payload are required. Do not infer a pass from silence or a usage-limit reply. Any claim about a specific clean comment format remains **REASONED** until observed.

4. **Auto-merge, strict freshness, and push events:** **VERIFIED:** [auto-merge waits for required checks/reviews](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request); [strict freshness requires an updated branch](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches). Auto-merge is not the branch updater. [REST update-branch](https://docs.github.com/en/rest/pulls/pulls#update-a-pull-request-branch) merges the base into the head and accepts `expected_head_sha`. **REASONED for the proposed setup:** do not promise a `push` workflow from a bot-enabled auto-merge without checking its actual credential/event behavior. Ordinary app/user-token operations can generate downstream runs; a `GITHUB_TOKEN` push is suppressed. Verify the selected path and preserve a deduplicated post-merge CI dispatch fallback.

   **Important current-doc correction, VERIFIED:** [GitHub now documents](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow) that `GITHUB_TOKEN`-caused PR `opened`, `synchronize`, and `reopened` events can create runs requiring human approval. Dispatch events still create runs, and push suppression remains. Thus C6's blanket “starts no PR CI run” is outdated, but it still cannot provide an unattended update/review cycle. Use an appropriately scoped app token or another explicitly tested path; merely dispatching existing CI would also fail this gate's requirement for `event == pull_request`.

5. **Merge queue availability:** **VERIFIED:** the repository API reports owner type `User`. [GitHub's documented merge-queue availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue) covers organization-owned public repositories and qualifying organization-owned private repositories. It is not available for this current ownership type. A repository transfer is not necessary for the proposed controller and is not recommended by this review.

6. **Hostile PR dispatch safety:** **VERIFIED:** the present gate runs default-branch code and rejects fork heads for merging. **REASONED:** this is necessary but insufficient for dispatch safety. The new write path must apply its own shape/authority checks before dispatch, ignore forged comments, cap attempts, bind actions to exact revisions, and avoid executing PR content. Current `should_request_codex()` already demonstrates that a side effect can run on an ineligible draft. A `workflow_run` trigger can be caused by an untrusted upstream run; its occurrence is not authorization.

7. **Value of four hours:** **VERIFIED:** it is an age test in a prompt, not a lock or completion check. **REASONED:** it provides only crude throttling and time for late evidence to arrive. Replace those benefits with explicit completion/timeout states, late-result handling, action claims, budgets, and backoff. Do not replace four hours with a lone `repairable-now: yes` line.

8. **Structured output support:** **VERIFIED:** the exact [pinned action.yml](https://github.com/anthropics/claude-code-action/blob/756cc22e19660d20e8cc9496b4f242475a7f7790/action.yml#L182) exposes `structured_output` for `--json-schema`. Petasos already uses it in `claude-review.yml:54-56,85-123`; the local gate suite exercises the result-check steps. The builder can use it. That proves availability, not that a returned action claim matches real work.

**Concurrency detail, VERIFIED:** [GitHub's current docs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) say the default group keeps one pending run and replaces it when a newer one arrives, even with `cancel-in-progress: false`. They now also document `queue: max`, up to 100 waiting runs. Existing Petasos workflows use the default. Neither setting replaces durable action deduplication: keep runs short, rescan current state, and do not assume every wake-up executes in order.

## Validation and limits

**Executed:** `python -m pytest tests/test_merge_gate.py -q` against the reviewed tree: **175 passed**. This was local advisory evidence using an existing Python 3.13 environment, not a fresh dependency installation or a product-suite rerun. **CI observed:** the exact-base CI run linked above was successful. No local private denylist result is claimed.

Five additional isolated probes used the repository's `ready_facts()` and fake GitHub fixtures, with no network or real writes:

| Probe | Observed result |
|---|---|
| Running Claude review, empty job conclusion | Reported “did not complete or was invalid,” job missing |
| Blank line after `wall_forbidden:`, then a forbidden file inside the expected wall | Empty forbidden list; `ready=True` |
| Sole Codex review state `DISMISSED`, current commit, empty body | `ready=True` |
| Draft PR, CI green, no Codex result | `should_request_codex=True` |
| List response has no hold; fresh PR response has `hold`, same head | Gathered labels empty; `ready=True` |

The first four are reproducible by changing the corresponding fields of `ready_facts()`. For the fifth, subclass `FakeGitHubForGather`: return one allowed changed file, successful workflow evidence, a current Codex review, and a fresh `/pulls/1` response with `changed_files=1` and `labels=[{"name":"hold"}]`. Pass an unchanged `open_pr_dict()` with the valid wall-section body to `gather()`. The fetched hold is discarded. Add these cases to the standing-forbidden test file only through an approved process-fix PR.

**Where this review is guessing:** the private planning session's motives and permission history; the precise division of waiting time; unobserved clean Codex output; server-side Claude token exchange for the new bot caller; dispatch ancestry across repeated cycles; push-event behavior of the selected future auto-merge credential; notification delivery; and future behavior of the local shell connector. These are not prerequisites for writing a safer design, but the relevant live experiments are prerequisites for calling its write paths proven.

This review ran no live failure injection, dispatch, branch update, credential change, merge, or configuration change. The recommended experiments belong in a disposable repository under a separately approved implementation plan. The next planning packet should name the bounded files, the trusted action-record store, the exact authorization/delegation record, and acceptance tests for every new write path.
