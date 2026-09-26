# Response to the plan review of 2026-09-26

From: Claude (Cowork planning thread). Reviewed: `review/2026-09-26-plan-review.md` (pull request #1, branch `review/plan-review-2026-09-26`). Nothing here is ratified; this records what Claude agrees with, what it disagrees with and why, what was already fixed, and what waits for Elias.

## Verdict on the review

The review is right on the two findings that matter most. The trust spec hashed the manifest, which describes risk, not the action's actual arguments, so an approval did not bind the amount, the recipient, or the ticket version. And the executor had no atomic reservation, so one approval could run twice. Both are real gaps in the spec, not the code (there is no code yet). The review also caught four bootstrap and pipeline defects that would have stopped the builder on day one. Its product judgment (lead with one refund journey, prove it early) is better than the plan's (build the library bottom-up for a week, then integrate).

## Agreed and already fixed in this commit

| Finding | What changed |
|---|---|
| 4, bootstrap walls | `changes/000-bootstrap/plan.md`: `grounded_at` is the real seed commit `110b9a0`; the change's own `tasks.md` and `report.md` are in its wall; the "first commit" story is gone. AGENTS.md section 1 now says a builder may always write its own change's `tasks.md` and `report.md`, and section 2 says `grounded_at` is set by the planning thread at ratification, never by the builder. `changes/001-trust-core/plan.md` says the same. |
| 4, lint failure | `tests/conftest.py` import order fixed (the CI failure on `110b9a0` was ruff rule I001 on that line). |
| 4, uv commands | CI runs `uv pip check` (uv's own environment). `uv sync --locked` starts once 000 commits `uv.lock`; 000's plan says so. |
| 5, identifier gate passes when empty | CI now fails the `private-identifiers` job when the `PRIVATE_DENYLIST` secret is missing or empty, before running the test, and the test reports skips loudly. A skipped scan can no longer look like a passed scan. |
| 7, review workflow inputs | `claude-review.yml` installs the plugin through the action's `plugins` input and allows bot-opened pull requests through `allowed_bots` (both inputs checked against the action's `action.yml` on 2026-09-26). The exact bot username still needs one live test. |
| 8, deploy on push | Both deploy workflows now run only on `workflow_dispatch` with a required `ref` input. Merge and release are separate acts. |

## Agreed, to be written into the specs in the next planning thread

- **1, the approval contract.** Adopt the review's replacement principle word for word: a grant authorizes one immutable action record (validated tool arguments, resolved destination, amount and currency where relevant, resource version, proposer, policy version). Risk is derived from server-owned tool definitions plus those arguments. The approval card and the executor read the same record. Changed arguments or a stale resource version require a new proposal. This replaces spec 1.1, 1.18, and the hash rule in ARCHITECTURE.md section 4.
- **2, one execution owner.** Execute inside one database transaction that moves the grant `APPROVED -> EXECUTED`, records the fake effect, and appends the ledger row together; a retry of an executed grant returns the recorded outcome. To make that transaction possible, v1 uses one SQLite file (trust tables, ledger, help-desk tables, memory) with clear module boundaries instead of three files. The three-file split was carried over from a system where the bus was readable by tools; here the same process opens every file, so the split bought no security and cost atomicity.
- **6, queue recovery.** One builder run at a time (concurrency group). Eligibility excludes any change whose `[build] NNN-slug` pull request is already merged (derived from GitHub, so a merged change can never become eligible again before close-out). Claims expire: a `build/NNN-slug` branch with no commit in 6 hours and no open PR is deleted by the next run. A run that fails twice on the same change stops and writes one entry in `changes/QUESTIONS.md`.
- **9, the browser journey.** Change 004 gains browser endpoints for reading tickets and proposing an action, served by the same application service the MCP tools call, plus a guided scenario button ("Ask for a $42 refund") that produces a request to approve. Keyboard approval: hold the space bar, or a plain button where the hold is not usable. D-009 already allows a button.
- **11, spec gaps.** Rail accounting: staging reserves the slot; approval does not charge again; abort and expiry release it. Token uniqueness: a unique index on `token` alone, not `(verb, token)`; the conditional update is what guarantees single consumption. `abort(token, approver)`. Hard-constraint path patterns listed in the spec. `PUBLISH` and `REVEAL-SECRET` are removed from the demo's verb table (no demo tool can produce them). Expected-tier tests for every row of the decision table, not only monotonicity.
- **12 and 13, honest claims.** Tier filtering is the primary control; canaries are a detector with stated limits, scanned before any byte leaves and before any outbound fake effect is recorded. Ledger verification is described as "the chain is internally consistent"; retained ledger rows never contain visitor-supplied text (ids, verbs, tiers, hashes only), which also resolves the hourly-deletion promise against seven-day retention.
- **14, prove the SDK early.** Change 000 gains one authenticated MCP tool (`ping`) behind the identity middleware, so the `mcp` 2.x server API, routing, and lifespan are proven before 003 depends on them.
- **15, schedule.** Changes 007 and 008 leave v1. The README storyboard moves to the first week. Days 12 to 14 are reserved for failure cases, accessibility, evidence, and repair.

## Disagreements, with reasons

- **3, remove approval tools from the MCP surface.** Not adopted. Approval tools are already visible only to the `owner` identity (invariant 18); the `agent` identity never sees them, which is the property the review asks for. Removing them from MCP entirely would leave curl-only reviewers with no way to complete the third rung. What changes is the claim: the README and product docs will say "a proposer credential can never approve; the public owner credential is a simulation role, not proof of a human." The framing edit is accepted; the surface change is not.
- **7, drop the second AI review if it costs more than it catches.** Not yet. Both reviews run on subscriptions already paid for. Keep both through the first three pull requests, then decide on evidence.
- **Alternative 3, single application service behind both interfaces.** Adopted (see finding 2 above), but the library stays a proper package inside the same repo. Independent distribution is not a v1 goal, which is what the review said.
- **Alternative 4, deterministic public walkthrough with optional live MCP.** Adopted as the browser scenario; the live MCP path stays a first-class rung, not optional, because it is the rung that proves the gate to an engineer.

## What waits for Elias

Three decisions, each with the recommendation first. Plus one action.

1. **Release promise.** Recommend: the exact-refund-approval journey is the v1 promise; the canary demonstration is second; 007 and 008 leave v1; one SQLite file. Amends D-001 and D-011.
2. **Merging and releasing.** Recommend: Elias merges every pull request and starts every release by hand for all of v1; auto-merge is not a v1 goal. Amends D-006 (the "week two" clause is dropped).
3. **Visitor isolation.** Recommend: the demo mints a visitor session (three tokens, its own data namespace, one-hour life) instead of publishing three static tokens; the README publishes the one-line curl that mints a session. Global abuse ceilings stay. Amends D-010.
4. **Action:** merge pull request #1 so the review lives on `main` under `review/`.

Once these are answered, the next planning thread rewrites `changes/001-trust-core/spec.md`, ARCHITECTURE.md sections 3 to 6, D-001, D-006, D-010, D-011, and the proposals for 000 (add the MCP `ping`), 003, and 004, then asks the ChatGPT reviewer for a second pass on the rewritten spec before ratification.
