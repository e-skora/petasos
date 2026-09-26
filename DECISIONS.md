# DECISIONS.md: important decisions and why

Append-only. Newest at the bottom. A decision is either PROPOSED (Claude drafted it, Elias has not ruled), RATIFIED (Elias said yes, in his words, on the date shown), or SUPERSEDED (points at the decision that replaced it). Agents implement RATIFIED decisions and never reopen them; a task that seems to require reopening one is a stop-and-flag (see AGENTS.md).

Format: `D-NNN | status | date | decision | why | alternatives rejected`.

---

## D-001 Product framing
- Status: PROPOSED
- Decision: Petasos is a **trust gate for AI agents**: a Python library (`petasos`), a reference MCP server with a fake help desk, and a demo app plus site at petasos.io. It is a reference implementation and says so.
- Why: the July spec (archived) was a two-tool help desk showing three tiers. The Talaria scan found the safety layer is the real differentiator and is worth showing whole: rule-derived tiers, single-use grants, canary leak detection, fail-closed MCP auth, tamper-evident audit, press-and-hold approvals. A library framing lets a reviewer read one module at a time; the help desk is only the stage set.
- Rejected: "port all of Talaria" (66k lines, personal, unshippable in two weeks); "just the MCP demo" (too thin to carry the takeaway sentence).

## D-002 Name and voice
- Status: RATIFIED by Elias 2026-09-26 ("we'll still call it petasos")
- Decision: The project is **Petasos**. The demo help desk is "Hermes Helpdesk" (fictional). Talaria is named openly as the private system the design is drawn from.
- Why: the sibling-artifact story (winged hat, winged sandals) explains the lineage without claiming shared code.
- Rejected: version-style names (`talaria-lite`, `talaria-v1.1`) which imply extraction.

## D-003 Language and stack
- Status: RATIFIED in principle by Elias 2026-09-26 ("yes fly.io or railway with cloudflare works", accepting the Python recommendation); details PROPOSED
- Decision: Python 3.12+, FastAPI, the official `mcp` SDK (exact pin, `pip check` in CI), SQLite (WAL) for every store, `uv` for environments, `ruff` for lint and format, `pytest` plus `hypothesis`, `src/` layout. Demo UI: Preact + Vite + TypeScript, Vitest, `preact` as the only runtime dependency. Site: static HTML/CSS built by the same Vite project or plain files.
- Why: Python matches Talaria and the rest of Elias's tooling, so the agents reuse proven patterns and tests port cleanly; the July "learn TypeScript" motive is gone because agents write the code. The MCP SDK pin is a Talaria lesson (a transitive bump once broke the web stack). SQLite keeps the demo to one process with no managed database.
- Rejected: TypeScript on Cloudflare Workers (free, but a wider rebuild gap); Cloudflare Python Workers (reached general availability on 2026-09-21, too new to bet a two-week deadline on).

## D-004 Hosting and domains
- Status: PROPOSED (Elias bought petasos.io 2026-09-26 and approved Fly or Railway plus Cloudflare)
- Decision: the API and MCP server run on **Fly.io** (one `shared-cpu-1x` machine, about $2/month, auto-stop off so the demo is always warm) at **api.petasos.io**, DNS-proxied through Cloudflare (orange cloud) for edge rate limiting and WAF. The site and demo app are static on **Cloudflare Pages** at **petasos.io** (free). Deploys run from GitHub Actions on merge to `main`.
- Why: cheapest always-on Python box; Cloudflare proxy protects a server that publishes its own demo tokens; Pages is free and fast. A Cloudflare Tunnel sidecar was rejected because it bypasses Fly's proxy.
- Rejected: Railway Hobby ($5/month flat; fine, but Fly is cheaper and the auto-stop story is documented); running the API on Pages Functions (no long-lived process for the ledger).

## D-005 Repository
- Status: PROPOSED
- Decision: public GitHub repo `e-skora/petasos` from the first commit. Local clone at `~/code/Petasos` on Elias's Mac (his standing rule: repos live in `~/code/<name>`; the folder was created 2026-09-26). The ChatGPT reviewer project keeps its own clone of the same repository in its own folder (a sibling clone, not a fork), works on `review/*` branches, and writes into `review/`. The planning folder that drafted this doc set keeps its private scan notes under a git-ignored `_private/` directory that never leaves the Mac.
- Why: the account URL goes on the resume; building in the open is part of the story.

## D-006 The autonomous build loop
- Status: PROPOSED
- Decision: two agents, two jobs, one human.
  - **Claude Code** is the builder. It runs in the cloud through the official GitHub Action on a schedule (every 2 hours) and on `@claude` mentions, authenticated with a Claude Max OAuth token (no API key). It drains ratified changes from `changes/`, builds on a branch, and opens a PR carrying a return report.
  - **Codex** is the independent reviewer. The Codex GitHub integration reviews every PR automatically under the ChatGPT Pro subscription, and `@codex` comments delegate fixes. Codex may also build `lane: codex` changes as cloud tasks kicked off from chatgpt.com/codex.
  - **Claude's code-review plugin** runs through the same Action on every PR for a second, cheaper review.
  - **GitHub branch protection** on `main`: required checks (the CI jobs `python`, `demo`, `private-identifiers`), one required approving review, linear history. The Claude app and the Codex app are different identities, so one can approve the other's PR.
  - **The ChatGPT reviewer project** (ChatGPT desktop app with Codex and local access to its own clone) is the product-intelligence and acceptance role: it critically reviews every proposal and spec BEFORE Elias ratifies, hunts gaps and viability problems, proposes alternatives, and can draft documents on `review/*` branches. Its outputs live in `review/` and reach the builder only after Elias merges them. It never edits the doc set on `main` directly.
  - **Elias** ratifies proposals, answers `changes/QUESTIONS.md`, and clicks merge in week one (after auto-merge is on, he reads reports instead). Nothing is ratified until the ChatGPT reviewer has reviewed it (Elias, 2026-09-26). In week two, if no PR needed a human fix, auto-merge is switched on and his job is reading reports.
- Why: everything runs on subscriptions he already pays for, nothing depends on his Mac being awake, and CI runs the real test suite (the thing Talaria's cloud builder could never do, which is why its numbers were "advisory"). Verified against the vendors' current docs on 2026-09-26; the workflow files cite the doc URLs.
- Known limit: Codex cannot be scheduled from CI on the Pro subscription without an API key; that is why Claude Code holds the scheduled builder role and Codex holds review.
- Rejected: Claude Code Routines as the scheduler (works, but the GitHub Action lives in the repo where a reviewer can read it); an OpenAI API key (cost, and not needed).

## D-007 The change process
- Status: PROPOSED (structure requested by Elias 2026-09-26)
- Decision: every meaningful change is a folder `changes/NNN-slug/` with `proposal.md` (why, what, not), `spec.md` (binding decisions and acceptance tests), `plan.md` (file lane, walls, order, stop-and-flag conditions), `tasks.md` (the checklist the builder drains), and after the build `report.md` (what landed, test delta, deviations, review findings and their disposition). A `status:` field on the proposal moves `proposed -> ratified -> building -> in-review -> merged`. Only Elias moves it to `ratified`. The builder never edits a status; build state is derived from branches and PRs.
- Rules carried from Talaria's factory: claim-before-work with an empty commit; a mechanical file-wall check before push; freeze the commit, then review, then fix; a return report with zero placeholders (grep-gated); agent-authored proposals only for forced fixes, everything with a judgment call becomes a dated question in `changes/QUESTIONS.md`.
- Why: chat is ephemeral; the folder is the memory. Derived state avoids the lost-status-flip bug class Talaria hit.

## D-008 Hygiene: patterns, not identifiers
- Status: PROPOSED (continues the July rule)
- Decision: a test (`tests/test_no_private_identifiers.py`) greps the whole repo for a denylist: the private project's hostnames, GitHub path, service labels, secret-store slot names, historical canary prefix, client header name, env var prefix, home-directory paths, and the names of people in Elias's life. Talaria the word is allowed. **The denylist is itself private**, so it is never committed: CI reads it from the GitHub secret `PRIVATE_DENYLIST` (one entry per line); a local run without the secret skips the test with a visible warning. The master copy lives in the git-ignored `_private/` directory of the planning folder.
- Why: the scan found real names, hostnames, and paths in code comments and reports; a rule without a test drifts; and publishing the list would publish the identifiers.

## D-009 Approvals are a button or press-and-hold, never a typed phrase
- Status: PROPOSED (inherits Elias's Talaria ruling of 2026-08-19, enforced 2026-09-03)
- Decision: in the demo app, approval is press-and-hold (1.2 s for L4, 2.5 s for L5). Over the API, the owner client submits the grant's token; no phrase composition exists anywhere, and no response ever carries a ready-to-type string.
- Why: Talaria ruled typed phrases dead three times; a copy-pasteable credential is a leak shape.

## D-010 Demo data and public tokens
- Status: PROPOSED
- Decision: fictional company and customers (no real brand, no real person); three demo tokens published in the README; per-token rate limits (30 reads/min, 5 staged writes/hour, 2 L5 stagings/hour), which stack on top of the trust core's global per-verb-family rails from change 001 (two independent layers, both apply); fake data reset hourly; nothing a visitor types survives the reset. One canary ticket, never listed.
- Why: public tokens are what make rung 3 self-serve; limits and resets keep bots from burning the box.

## D-011 Scope walls (v1)
- Status: PROPOSED
- Decision: out of v1: vector memory, model routing and cost ledger, judge quorum, relay engine, eval harness, chat/projects/files, scheduler, skills, real email or payments, OAuth, dark mode, push. Listed as v2 candidates: relay engine, eval harness with calibration rows, OAuth 2.1.
- Why: two weeks. Each excluded item either needs live vendor keys to demo or is not part of the safety story.

## D-012 License
- Status: PROPOSED
- Decision: MIT.
- Why: simplest for a portfolio reference implementation.
- Rejected: Apache-2.0 (patent clause is irrelevant here; longer text).

## D-013 Tests are the merge gate, never an agent's claim
- Status: PROPOSED
- Decision: CI runs the full suite on every PR; an agent's reported test count is never restated as fact; the PR check is the only number that counts. A suite number always names the commit it was measured at.
- Why: Talaria's "shipped is not live" lesson, and its cloud builder's advisory-only numbers.

## D-014 Design language
- Status: PROPOSED (details in DESIGN.md)
- Decision: light theme only; trust tiers are a semantic palette (grey L1 to L3, amber L4, crimson L5) and tier is never conveyed by hue alone; crimson means only "L5 stakes or failure"; the pending-approvals pill is chrome on every screen, never hidden in a menu; a refusal is never rendered as "nothing waiting"; every label is plain language.
- Why: these rules are what make the safety model legible to a non-engineer in two minutes, and each one closed a real bug in Talaria.

## D-015 Builds merge without Elias
- Status: RATIFIED by Elias 2026-09-26 ("i need the merging to happen without me, id ont have time to merge every single build")
- Decision: no human merges a build. `main` is protected with required checks and zero required human reviews. A deterministic **merge gate** (a scheduled GitHub Actions workflow, every 15 minutes, a script with no model in it) squash-merges an open, non-draft `[build] NNN-slug` pull request when all of these hold on the PR's current head commit: the CI jobs `python`, `demo`, `private-identifiers` are green; the Claude review and the Codex review have each posted on that head commit and neither contains a `blocker`; `changes/NNN-slug/report.md` exists with `Verdict: BUILT` and no placeholders; the PR body carries the wall-check output. Anything else waits, and a PR that has waited through two builder runs with an unresolved blocker is repaired by the builder's next run (queue-recovery rule). Planning PRs (doc set, specs) are merged by Claude from the Mac after the reviewer's pass; Elias merges nothing. Releases (deploy workflows) stay a separate hand-started act, a few clicks per project, not per build.
- Why: Elias's time is the scarce resource; the gate replaces his click with checks a script can verify. Parsing the two reviews' comments removes the unproven question of whether a bot review counts as a formal GitHub approval.
- Supersedes: the "Elias merges in week one, auto-merge in week two" clause of D-006 and the week-one wording in D-007.
- Rejected: requiring a formal approving review from a bot (unverified behavior); GitHub's built-in auto-merge alone (it cannot read the review comments or the report).

## D-016 The v1 release promise
- Status: RATIFIED by Elias 2026-09-26 ("otherwise yes to everything", answering the recommendation in `review/2026-09-26-plan-review-claude-response.md`)
- Decision: the v1 promise is one complete journey: an AI proposes a refund, a person approves exactly that refund, the system executes only what was approved and records it. The canary demonstration is second. Changes 007 (explain) and 008 (intent gate) leave v1. Storage is one SQLite file with clear module boundaries so a fake effect, the grant transition, and the ledger row commit in one transaction. A grant authorizes one immutable action record (validated arguments, destination, amount and currency where relevant, resource version, proposer, policy version), never a risk description alone.
- Why: the plan review's first two findings (unbound arguments, non-atomic execution) and its schedule finding.
- Supersedes: D-001's module list where it conflicts; D-011's stretch list; ARCHITECTURE.md section 6 (three files).

## D-017 Visitor sessions instead of static public tokens
- Status: RATIFIED by Elias 2026-09-26 ("otherwise yes to everything")
- Decision: the public demo mints a visitor session on request (three tokens for the three roles, its own data namespace, one-hour life); the README publishes the one-line command that mints one, not fixed tokens. Global abuse ceilings stay on top of per-session quotas. The public owner token is documented as a simulation role, not proof of a human.
- Why: shared quotas let two visitors exhaust the refund demo for everyone; shared data let one visitor approve another's work.
- Supersedes: D-010's "three demo tokens published in the README" clause.
