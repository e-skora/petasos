# Petasos plan review, 2026-09-26

## 1. Verdict

**Good plan with changes. Keep Petasos, but do not ratify the current build package.** A small, working example of an AI proposing an action, a person approving its exact details, and the system recording the outcome is a strong portfolio piece. Python, SQLite, fake services, and no model API bill fit the constraints. The proposed package spends too much effort on the build machinery before proving that experience, and several safety promises exceed the specified controls. Fix the approval contract and bootstrap blockers first, prove one complete browser-to-server journey early, and keep human merge approval throughout this release. Two weeks is a reasonable target for that reduced release, not a verified estimate for everything currently listed.

Reviewed all tracked files at **`110b9a0bc867f06b4059b0083e98d62b1fc69de6`**, including proposals 000 through 006 and both complete build packages. The sibling clone was created from the existing public [repository](https://github.com/e-skora/petasos). Review branch: `review/plan-review-2026-09-26`. This document recommends changes; it ratifies nothing. Private background notes are not reproduced here.

## 2. Findings, ranked by impact

### 1. Blocker: approval does not cover the action's actual contents

**VERIFIED specification gap; REASONED execution risk.** In `changes/001-trust-core/spec.md`, decisions 1.1 and 1.18 hash the manifest, the structured description of risk. It contains no email body, recipient, refund amount, currency, or expected ticket version. `target` is explicitly free text for the ledger. The document therefore provides no binding between what the person sees and what the executor does. The promise in `DESIGN.md` section 2.3 that changed tickets are refused has no corresponding version check.

**Why it matters:** A $42 refund and a $4,200 refund can share every specified manifest field. Checking that description again cannot detect the changed amount. A local hash probe confirmed this limitation of the specified representation, not a defect in implemented code.

**Change:** Store an immutable action record containing validated tool arguments, resolved destination, amount and currency where relevant, resource version, proposer, and policy version. Derive risk from server-owned tool definitions and validated arguments. Generate the approval card from that same record. Execute only that record, after checking its hash and resource version. Test changes to each action-bearing field. Do not let a caller supply its own authoritative risk flags or approver identity.

### 2. Blocker: one approval can still lead to two executions

**REASONED.** Decision 1.8 serializes approval correctly. Decision 1.11 then says to check `APPROVED`, call the tool, and mark `EXECUTED`. It does not reserve execution atomically. Two workers can both observe `APPROVED`; a crash after recording a fake refund but before updating the grant can also leave an ambiguous retry. Separate trust and help-desk database files make this harder.

**Change:** Specify one execution owner and an idempotency key, an identifier that makes retries return the original outcome without repeating the effect. For this fictional demo, either commit the fake effect, grant transition, and audit event in one database transaction, or define a durable retry protocol across the two stores. Include concurrent execution, crash recovery, ledger-write failure, abort races, and reset races. Do not claim exactly-once effects on future real payment services from a local database test.

### 3. Blocker before launch: public owner credentials demonstrate roles, not human consent

**VERIFIED document behavior; REASONED security limit.** `PRODUCT.md` section 6.2 publishes all three roles' tokens, including the owner. `ARCHITECTURE.md` section 4 and proposal 003 expose approval to an owner MCP client. A model given that credential can approve programmatically. The hold gesture is a browser interaction, not proof to the server that a human authorized the request.

**Change:** Label the public service a simulation with fictional effects and publicly selectable roles. Keep the model-facing configuration proposer-only. Prefer a separate browser approval surface and remove approval tools from the normal model tool list. Explain that a real deployment needs protected approver credentials and an authenticated human session. Preserve the role switcher as a teaching device, with that limitation visible. Revise the absolute claim that a model can never approve.

### 4. Blocker: bootstrap cannot satisfy its own file restrictions

**VERIFIED.** `changes/000-bootstrap/plan.md` excludes its own task file, report, and plan from permitted writes, while requiring updates to all three. It also describes the claim as the first commit even though the repository already has history. The existing [CI run](https://github.com/e-skora/petasos/actions/runs/36270866189) failed on import ordering in `tests/conftest.py`, which bootstrap cannot edit. Change 001 also requires filling `grounded_at` in a plan outside its permitted writes.

**Change:** Have the planning owner repair the seed baseline and both file lists before ratification. Ground plans against actual commits before dispatch. Permit task ticks and reports explicitly. Define a narrow, explicit rule for updating the base commit if that remains a builder task. Use `uv sync --locked` after committing the lockfile and `uv pip check` to target uv's environment directly. The workflow corrections must be owner-approved planning edits, not builder improvisation.

### 5. Blocker: the private-identifier gate currently passes without checking

**VERIFIED in GitHub CI.** The `private-identifiers` job at the reviewed commit succeeded with **`1 skipped`** because `PRIVATE_DENYLIST` was empty. Lines 59 through 62 of `tests/test_no_private_identifiers.py` skip in CI as well as locally. A successful check therefore does not establish that private identifiers were checked. Its walker also excludes directories and its own file, so “the whole repo” overstates coverage.

**Change:** Fail the trusted release gate when the list is absent or empty. Report separately whether the scan ran and passed. Define a safe contribution path for changes without access to secrets; do not run untrusted contributor code with private credentials to make the check green. Cover all publishable tracked text, document binary and history limits, and perform a private check before publishing. A scan after a public push cannot undo disclosure.

### 6. Blocker for unattended operation: the queue has no complete recovery or repair loop

**VERIFIED omissions; REASONED failure modes.** The builder selects a ratified proposal with no open matching pull request. It does not explicitly require merged dependencies, a matching lane, a complete reviewed spec, or exclusion of previously merged work. Until manual close-out changes status, a merged change can become eligible again. An abandoned claim branch can stop progress indefinitely. An open failing pull request is excluded from selection, and nothing automatically assigns its fixes. There is no builder concurrency control or claim expiry.

**Change:** For v1, run one builder at a time. Define eligibility and state from exact branch and pull-request records, including dependencies and completed work. Resume a recoverable claim, repair an existing failed change, or raise one concise exception. Add a run timeout, retry limit, and stop after repeated failure. Treat plan drift by shared interfaces as well as filenames. The process should save Elias time instead of requiring him to discover stalled work.

### 7. Blocker for the proposed merge loop: review comments are not a proven approval gate

**VERIFIED repository state and vendor documentation; account behavior untested.** GitHub returned “Branch not protected” for `main`, and the repository ruleset list was empty. The three required checks are a plan, not an active merge restriction. [OpenAI's GitHub documentation](https://learn.chatgpt.com/docs/third-party/github) establishes automatic review and comments, but does not establish a formal approval satisfying this repository's required-review rule. Distinct bot identities alone do not prove that integration behavior.

There are two additional concrete setup gaps. The Claude review workflow does not install the plugin it invokes, although the [action exposes a `plugins` input](https://github.com/anthropics/claude-code-action/blob/main/action.yml). It also lacks an explicit allowed bot actor. [Anthropic documents](https://code.claude.com/docs/en/github-actions) that bot-triggered runs are rejected unless allowed. That matters when the builder itself opens the pull request.

**Change:** Keep Elias's merge decision for this release. Prove a small builder pull request gets CI, both intended reviews, fixes, and a new review of the final commit. Check the actual GitHub review state and permitted approver identity, including whether a sole owner can satisfy the chosen rule. Protect `main` and enforce the file restriction from trusted policy. A prompt asking the builder to check its own files is not mechanical enforcement. Drop the second AI review if maintaining it costs more than it catches.

### 8. Blocker before release: deployment starts before readiness or acceptance

**VERIFIED.** The initial [Deploy API run](https://github.com/e-skora/petasos/actions/runs/36270866180) attempted deployment and failed because the hosting token was absent. The workflow runs on relevant pushes to `main`, independently of CI. Bootstrap calls its hosting files inert, but adding credentials would not make the workflow wait for change 005 or a usable server. The site workflow has the same independent release pattern.

**Change:** Keep deployment disabled until an explicitly approved release. Deploy a named, tested commit, serialize releases, and require a health check plus a representative action journey afterward. Record how to restore the prior release and preserve compatible data. Keep credentials unavailable to untrusted pull-request execution. Merging documentation, approving a plan, and authorizing a live release are separate actions.

### 9. Should-fix: the browser demo cannot yet complete the advertised journey

**VERIFIED interface gap; REASONED product impact.** Proposal 004 defines approval and ledger endpoints, but no browser-facing ticket reads or action-proposal endpoints. Browsers are excluded from `/mcp`. The design has ticket viewing and approval, but no complete way for a fresh visitor to create the request they should approve. A model API is unnecessary, but curl should not be the only way to start the story.

**Change:** Add a guided scenario button: “Ask for a $42 refund.” Show the request, why it waits, the exact approval, and the fake result. Browser and MCP adapters must call the same application service. Specify authentication, allowed browser origins, cross-origin request handling, and role checks for every browser endpoint. Include keyboard cancellation, focus loss, repeat key events, and a deliberate accessible approval alternative that does not require maintaining a hold. Promote factual explanations into core scope; decision 1.16 already supplies much of them.

### 10. Should-fix: shared public quotas make the demo easy to exhaust

**VERIFIED limits; REASONED visitor impact.** D-010 gives the shared public agent token two L5 stagings per hour and five writes. Two visitors can consume the refund demonstration for everybody. Shared approvals and hourly resets can also let one visitor approve, delete, or invalidate another visitor's work. Request-size, response-size, outstanding-request, and storage bounds are unspecified. Change 009 is labeled hardening even though parts of it are launch requirements or already assigned to 005.

**Change:** Prefer a small, temporary workspace per visit, with session quotas plus a global abuse ceiling. If that costs too much, ship a guided local simulation alongside the shared live server and disclose the shared limits. Keep payload bounds, expiry, reset behavior, and abuse controls before public release. Do not clear global abuse counters whenever demo fixtures reset.

### 11. Should-fix: several trust-core rules still require builder decisions

**VERIFIED.** Decision 1.9 names the same rate-limit category on staging and approval but never defines whether each consumes capacity. The fifth staged email could become impossible to approve if approval charges the same counter again. Decision 1.12 does not supply its forbidden-path patterns. Decision 1.10 omits the caller in the abort interface, although 1.17 requires authorization. All produced verbs must have executors, but publishing and revealing secrets have no defined demo behavior.

A local SQLite probe also showed that the specified unique index on `(verb, token)` permits the same token under two different verbs. It guarantees pair uniqueness, not the stated token uniqueness. Random collisions are unlikely; the claimed database guarantee is still incorrect.

**Change:** Define quota accounting and atomic reservation, including expiry and abort. Make token uniqueness match token-based lookup, and make conditional state transitions responsible for single consumption. Specify all authorization-bearing interfaces, invalid-manifest behavior, and unsupported tools. Test the decision table's expected tiers as well as monotonicity: a classifier returning L5 for everything would pass a monotonicity-only test. Include abort, global abort, boundary-time, and malformed-input cases.

### 12. Should-fix: canaries are a useful tripwire, not general leak prevention

**REASONED security limit, based on the specified guard.** Searching for a hidden marker catches payloads containing that marker. It cannot establish that private content was not paraphrased, sliced away from the marker, or encoded. Scanning an outgoing response also does not stop a leak already written through a fake email executor. If a streaming response releases bytes before its full check, aborting afterward is too late.

**Change:** Make authorization and memory-tier filtering the primary control. Describe canaries as an additional detector with explicit limits. Define coverage of responses, errors, logs, ledger views, and outgoing tool arguments. Prefer bounded JSON responses for this demo and scan before sending any bytes or recording an outbound effect. Test split markers, scan failures, alternate serialization, and content omitted from the marker. Reconcile the owner's T4 ceiling with the rule that every T4 entry is canaried and never released intact.

### 13. Should-fix: the ledger claim and hourly deletion promise are too broad

**VERIFIED contradiction and local chain probe; REASONED retention risk.** Architecture section 9 says every written table is chained; trust spec 1.14 says only the ledger is. A hash chain, a sequence where each row includes a fingerprint of its predecessor, detects an uncorrected edit. My small independent probe changed a row and recomputed later hashes successfully. Without an independently held checkpoint, it does not prove history was never rewritten or its end removed. The UI statement “Nothing has been changed” is too strong.

Architecture section 6 keeps ledger history for seven days while D-010 promises visitor input disappears hourly. Free-text targets and `detail_json` could retain that input. Resetting canary registration before old guarded data is gone creates another gap.

**Change:** Say “The current chain is internally consistent” and state the threat model and table coverage. Keep raw visitor text, approval credentials, and canaries out of retained audit details. Define reset ordering, active-request behavior, historical canary handling, daily chain boundaries, and retention across logs and backups. Demonstrate deliberate tampering on a disposable copy, without exposing an edit-history capability on the public service.

### 14. Should-fix: SDK availability is verified, client compatibility is not

**VERIFIED dependency installation and documentation; integration untested.** CI installed the pinned packages, including `mcp==2.2.0`; it stopped at lint, before the dependency consistency check and full tests. The [official release page](https://pypi.org/project/mcp/2.2.0/) confirms the version exists. The [migration guide](https://py.sdk.modelcontextprotocol.io/migration/) confirms a renamed server class and moved transport settings. Installation does not prove the proposed routing, lifespan, identity, or guard design works.

[Codex configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) supports bearer tokens and custom headers, and [Claude Code](https://code.claude.com/docs/en/mcp) documents bearer-header setup. That does not prove every Claude or ChatGPT client supports the handoff's paste-a-URL-and-token experience. The extra presented client identity is not specified as a concrete header or token format.

**Change:** In bootstrap, prove one minimal authenticated MCP tool and document exact tested client versions and setup steps. Verify startup, shutdown, both path spellings, unsupported origins, reconnects, and production host configuration. Pin the resolved dependency tree. Retain the useful host-lifespan rule, but test routing behavior instead of treating a ban on one mounting primitive as proof of safety. Avoid a hosting rewrite during this deadline.

### 15. Should-fix: the schedule delivers integration and the hiring story too late

**VERIFIED missing build packages; REASONED schedule and portfolio judgment.** Changes 002 through 006 have proposals but no specs, plans, or tasks. The scheduler cannot invent those within its rules. The browser and case study arrive after substantial infrastructure, leaving little time to learn whether a reviewer understands the point. A fresh AI repeating the desired takeaway is a weak success measure.

**Change:** Keep the existing change folders, but move a complete refund journey and a rough README storyboard into the first few days. Prepare the next reviewed package before the current one finishes. Freeze stretch features. Reserve the final days for deployment, failure cases, accessibility, evidence, and repair. Evaluate whether a reader can explain the prevented mistake, identify what approval covered, and name one limitation. Show Elias's decisions and tradeoffs; the number of agents involved is supporting evidence, not the headline.

## 3. Better alternatives and their tradeoffs

1. **Recommended framing: “An AI can propose a refund. It cannot change what you approved.”** Keep “trust gate” as the technical subtitle. Lead with one customer-support problem and observable before/after behavior. Tradeoff: a narrower headline, but a much clearer demonstration of engineering judgment.
2. **Build one complete journey first.** Use the existing Python library, one fictional refund, one approval card, and one ledger trail, reachable from the browser and MCP. Add the canary demonstration afterward. Tradeoff: less early breadth, earlier evidence that the parts fit.
3. **Use one application service behind both interfaces.** Keep a modular Python package, but treat independent library distribution as a later concern. Consider one SQLite database with strict repository interfaces for atomic fake effects. Tradeoff: weaker physical separation, simpler consistency. Database separation alone is not a security boundary when the same process can open both files.
4. **Use a deterministic public walkthrough plus optional live MCP.** No paid model is needed in the hosted path. A short recording of a real external MCP client can prove integration separately. Tradeoff: the default walkthrough must clearly identify the simulated proposer.
5. **Keep the current hosting direction, but reduce operations.** Python on Fly remains reasonable; do not switch runtimes merely for a lower headline bill. Budget the complete service, including storage and backups, rather than describing the machine estimate as the total. [Fly pricing](https://docs.fly.io/about/pricing/) lists those separately. Tradeoff: some hosting cost in exchange for a familiar deployment model. Actual memory usage and the under-$10 budget remain to be measured.

## 4. Ranked changes to the plan

| Order | Edit location | Required edit |
|---|---|---|
| 1 | `changes/001-trust-core/spec.md` decisions 1.1, 1.8, 1.11, 1.18; `ARCHITECTURE.md` sections 4 to 6 | Bind immutable action contents and resource versions; define execution ownership, retries, and atomic fake effects. |
| 2 | `PRODUCT.md` sections 1, 6.2, 7; `DESIGN.md` sections 2.2 and 2.6; D-009 and D-010 | Separate the public role simulation from real human authorization. |
| 3 | `changes/000-bootstrap/plan.md` Wall and grounding; 001 plan; `AGENTS.md` sections 1 to 3 | Repair the baseline, writable paths, real base commits, and reviewer-role exception. |
| 4 | Identifier test; `.github/workflows/ci.yml`; D-008 | Make a missing scan fail the trusted gate, define publication coverage, and protect private input. |
| 5 | Builder and review workflows; `CLAUDE.md`; D-006 and D-007 | Add queue recovery, repair assignment, plugin setup, bot handling, trusted file checks, and a proven merge policy. |
| 6 | Deployment workflows; 005 proposal and future spec | Separate release permission from merge and deploy the tested commit with recovery evidence. |
| 7 | 004 proposal and future spec; `DESIGN.md` section 2 | Add browser ticket/action interfaces and the guided refund journey with accessible approval. |
| 8 | D-010; `ARCHITECTURE.md` section 6; 005 proposal; `changes/README.md` | Decide visitor isolation, quotas, retention, reset ordering, and which launch controls belong to which change. |
| 9 | 001 spec decisions 1.9 to 1.17 and acceptance tests | Resolve quota accounting, token uniqueness, abort authorization, hard constraints, and missing failure tests. |
| 10 | 002 and 003 future specs; `ARCHITECTURE.md` invariants 11 to 18 | Define guard coverage and limitations, bounded output, memory visibility, and supported clients. |
| 11 | `ARCHITECTURE.md` section 9; `DESIGN.md` section 2.4; 001 spec 1.13 and 1.14 | Narrow ledger assurance and make retained audit details content-free. |
| 12 | `PRODUCT.md` sections 4, 7, 9; `changes/README.md`; 006 proposal | Bring the scenario and story forward, defer stretch work, reserve repair time, and measure reader understanding. |

Proposed replacement principle for the approval contract:

> A grant authorizes one immutable action with exact validated arguments. The approval screen and executor use that same record. Changed arguments or stale resource versions require a new proposal. Retrying a completed grant returns its recorded outcome and never repeats the fake effect.

Proposed replacement principle for public claims:

> The public demo uses fictional data and simulated effects. Its role switcher shows how permissions work; public owner access does not prove human identity. Canaries detect included markers, and ledger verification checks internal consistency. Each control's limits are documented beside its demonstration.

## 5. Decisions for Elias

Only three product-level choices need your attention now. Implementation details above are recommendations for the planning owner to resolve into specs.

1. **Recommend making the exact-refund-approval journey the release promise.** Keep Petasos and the existing stack. Make canaries a second demonstration and defer intent parsing, separate explanation machinery, and an elaborate autonomous factory. Tradeoff: less breadth, a more dependable two-week result.
2. **Recommend retaining your explicit merge and release decisions throughout v1.** Automated checks and AI reviews prepare the decision. Do not switch to auto-merge because a calendar week elapsed. Tradeoff: a few short approval decisions, with fewer unproven assumptions about bot authority.
3. **Recommend a temporary workspace per visitor if a live shared demo is essential.** Otherwise choose the cheaper deterministic browser walkthrough with optional shared MCP access and clearly stated limits. Tradeoff: additional session-isolation work versus a less realistic public live experience.

## 6. Where I am guessing

- I expect a narrow, clear support-workflow demonstration to impress the intended hiring audience more than architectural breadth. That is product judgment, not hiring-market research.
- I estimate the reduced release can fit two weeks if setup and the first complete journey succeed early. Agent throughput, account limits, and Elias's daily decision time have not been measured.
- I expect per-visitor isolation to improve reliability, but I have not measured traffic or its implementation cost.
- Concurrency, stale approvals, leaks, and reset problems above are risks allowed by incomplete specifications. There is no application implementation at this commit on which to reproduce them.
- I do not know whether the connected Codex account can produce the precise formal approval this repository would require. Documentation is insufficient; test the actual integration.

## 7. Calibration note

**Checked:** the sibling clone, every tracked file, the exact base commit, public repository visibility, GitHub CI and deployment logs, branch protection and ruleset responses, and official vendor documentation linked above. Ran small standard-library probes of manifest-hash scope, the specified SQLite index, and unanchored hash-chain rewriting. Those probes test proposed mechanisms, not Petasos runtime code.

**Observed baseline:** dependency installation succeeded in CI; Python lint failed; later Python checks did not run; the demo job skipped its absent application; the identifier check skipped its missing input. No green application suite or working deployment is established.

**Not checked:** subscription entitlements or remaining allowances, GitHub App installations, live bot approval behavior, hosting and DNS account configuration, the private denylist contents, a browser app, MCP interoperability, load, or recovery of a running service. I did not read or copy private source code or reproduce private background notes in this review.

The clone now exists, so the handoff's access limitation is resolved. Next, after planning corrections and ratification, verify one small builder pull request end to end: exact dependency lock, real checks, completed independent review, recorded owner decision, and no automatic deployment. Then prove the authenticated MCP tool and browser refund journey. Those checks will reveal much more about viability than another layer of planning documents.
