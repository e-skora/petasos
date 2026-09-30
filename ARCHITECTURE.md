# ARCHITECTURE.md: how Petasos is built

Status: PROPOSED, version 0.3 (2026-09-29: invariant 1 reworded for the approval comparison, per `review/2026-09-27-specs-002-003.md` finding 6; 2026-09-26: sections 2 to 7 and 9 rewritten for D-015, D-016, D-017). Binding once ratified. Live code beats this document; when they disagree, fix one of them in the same PR and bump the version here.

Plain-language rule: every term is defined the first time it appears. If you find one that is not, that is a bug in this document.

## 1. The shape in one picture

```
                 +-----------------------------+
   curl / Claude |  api.petasos.io  (Fly.io)   |
   / Codex ----->|  FastAPI process            |
   (MCP client)  |                             |
                 |  [1] identity middleware    |  401 before any MCP handshake
                 |  [2] MCP server  /mcp       |  tools = help-desk actions
                 |  [3] trust gate             |  action record -> tier -> execute or stage
                 |  [4] memory + canary guard  |  every response scanned on the way out
                 |  [5] ledger (hash chain)    |  every decision recorded
                 |  [6] owner HTTP endpoints   |  /owner/* for the demo app, never /mcp
                 +-----------------------------+
                          ^          |
   browser ---------------+          v
   petasos.io (Cloudflare Pages)   one SQLite file on a Fly volume
   site + demo app (Preact)        (visitor data expires hourly)
```

Numbers in brackets are the layers a request passes through, in order.

## 2. Vocabulary

- **MCP**: Model Context Protocol, the open standard an AI assistant uses to discover and call outside tools. A Petasos server is one such tool provider.
- **Tool definition**: server-owned code for one action: its name, its risk profile, how to validate arguments, how to look up what it acts on, and its effect. A caller names a tool and passes arguments; it never supplies risk facts.
- **Risk profile**: the risk facts on a tool definition: what it mutates (nothing, internal data, or something outside), whether it is reversible, whether it moves money, whether it deletes. It has **no tier field**. A test pins that.
- **Tier (L1 to L5)**: how dangerous an action is. Derived from the risk profile by fixed rules. L1 read-only; L2 reversible internal write; L3 irreversible internal write; L4 external write (needs human approval); L5 money or deletion (needs a longer human approval).
- **Action record**: the exact action a person approves: tool, validated arguments, destination, amount and currency, the resource and its version, the proposer, and the policy version. Frozen when proposed; the approval card and the executor both read it.
- **Resource version**: a number the help desk bumps whenever a ticket changes. An approval of one version cannot run against another.
- **Memory tier (T0 to T5)**: how private a stored fact is. T0 public through T5 private-local. A different ladder from L1 to L5; they share no code.
- **Grant**: the record of a staged action: verb, single-use token, tier, expiry, the action record and its hash, state.
- **Verb**: a short word bound to the grant (`SEND-EMAIL`, `ISSUE-REFUND`, `DELETE-TICKET`). The human sees the verb; the approval must match it.
- **Client identity**: who is calling. Each identity has a bearer token, a memory-tier ceiling, and an action scope.
- **Visitor session**: a set of three short-lived identities (visitor, agent, owner) and a private data namespace, minted on request for one person trying the public demo. It lasts one hour.
- **Canary**: a random token quietly planted inside private memory text. If it ever appears in an outgoing payload, something leaked.
- **Guard**: the function that scans outgoing payloads for canaries and privacy-labelled keys and aborts on a hit.
- **Ledger**: the append-only audit log with a hash chain.

## 3. Package layout

```
src/petasos/
  storage.py         # Database: one SQLite file, WAL, explicit migrate(), transaction() = BEGIN IMMEDIATE
  trust/
    risk.py          # RiskProfile (no tier field), derive_tier, verb_for, rail_for, POLICY_VERSION
    tools.py         # ToolDefinition, ToolRegistry: server-owned; risk lives here, never with the caller
    record.py        # ActionRecord, canonical_json, record_hash
    grants.py        # GrantStore: stage, list, approve (compare-and-swap), abort, expire, rails
    executor.py      # run(grant_id): one transaction for the effect, the grant change, the ledger row
    constraints.py   # hard constraints above the ladder (deny regardless of tier)
    gate.py          # Gate.propose(tool, arguments, proposer): the one entry point for MCP and browser
    outcomes.py      # result code -> one plain sentence
  ledger/
    chain.py         # append(conn, row) inside the caller's transaction; verify() names the first broken row
    store.py         # schema; says what is chained and what verify() proves
  memory/
    store.py         # tiered entries, expires_at flag, per-client read ceiling
    canary.py        # mint, embed once, register; never re-mint
    guard.py         # assert_no_private_payload(payload, canaries): destination-blind
  mcp/
    identity.py      # ClientRegistry: token lookup keyed by presented client id, constant-time compare
    middleware.py    # ASGI: 401 before the MCP handshake; refuse any Origin header
    server.py        # streamable HTTP at /mcp as an exact Route (not a Mount)
    tools.py         # adapters: MCP tool call -> Gate.propose
    guard_seam.py    # the ONE place responses are scanned; UNGUARDED_ROUTES enumerated
  helpdesk/
    data.py          # fictional tickets with resource versions, per-session namespaces, the canary ticket
    tools.py         # the help-desk ToolDefinitions (validate, resolve, effect)
    fake_email.py    # records "sent" mail in a table; never sends
    fake_payments.py # records refunds; never moves money
  sessions/
    visitor.py       # mint a visitor session: three identities, a namespace, one-hour life
  owner/
    api.py           # browser endpoints for the demo app: tickets, propose, approvals, approve, abort, ledger
  app.py             # composition root: builds everything, mounts routes, owns lifespan
demo/                # Preact + Vite app (served from petasos.io)
site/                # static site
tests/
  test_no_private_identifiers.py   # reads the denylist from the PRIVATE_DENYLIST secret; fails in CI without it
  test_merge_gate.py               # the merge gate's rules, no network
  ...
.github/scripts/merge_gate.py      # the merge gate (D-015): no model, tested above
changes/             # the build ledger (see AGENTS.md)
```

The MCP adapters (`mcp/tools.py`) and the browser endpoints (`owner/api.py`) both call `Gate.propose` and the same `GrantStore` and `Executor`. There is one application service, two front doors.

## 4. Request flow for a risky action

1. An MCP client calls `issue_refund` with a valid `agent` token and arguments `{"ticket": 42, "amount": "42.00", "currency": "USD"}`. (The browser's "Ask for a $42 refund" button reaches the same place through `owner/api.py`.)
2. **Identity middleware** authenticates before the MCP layer sees the request. No token, wrong token, or any `Origin` header: bare 401, no tool list, no handshake.
3. The adapter calls `Gate.propose("issue_refund", arguments, proposer="agent")`. Hard constraints are checked first.
4. The server-owned tool definition validates the arguments and resolves them against current data: destination (the customer), amount 4200 in minor units, currency USD, resource `ticket:42` at version 3. These become the **action record**, frozen, with its hash, the proposer, and the policy version.
5. `derive_tier` reads the tool's risk profile (external, irreversible, money) and returns **L5** by rule. L4 and L5 always stage.
6. In one transaction, `GrantStore.stage` reserves a slot on the `money` rail, creates a grant in state `AWAITING` with verb `ISSUE-REFUND`, a random token, and a 24-hour expiry, and appends a `staged` ledger row.
7. The response to the client is `{"status": "staged", "verb": "ISSUE-REFUND", "tier": "L5", "explanation": "..."}`. **No token is returned to the proposer.**
8. The owner sees the approval card, built from the action record itself (refund $42.00 to the customer on ticket 42), and approves by press-and-hold in the demo app, which submits the token and verb.
9. `GrantStore.approve` runs one conditional update: `AWAITING` to `APPROVED` where token, verb, unexpired, and "approver is not the proposer" all match. Right token, wrong verb: the grant is **burned** (`REJECTED`, `verb_mismatch`). Wrong caller: refused, the grant survives.
10. `Executor.run(grant_id)` opens ONE transaction: recompute the record hash, check the policy version and the ticket's current version, call the refund effect with the grant id as its idempotency key, move the grant to `EXECUTED`, append the `executed` ledger row, commit. Any failure rolls all of it back. Running it again returns the first outcome and repeats nothing.
11. The owner gets a plain-language outcome sentence.

Every response on the way out passes the **guard seam** exactly once.

## 5. Invariants (each one is a test)

1. The risk profile type has no tier field, and no caller-facing entry point accepts risk facts, a tier, a verb, or an approver from request content. The one exception is narrow: an authorized approval entry point (`GrantStore.approve` and the owner's `approve` tool) accepts a claimed verb solely to compare it with the verb stored on the grant, burning the grant on a mismatch (section 4, step 9); it never sets or selects a verb, a tier, a record, or an approver.
2. `derive_tier` is pure and monotone (adding any risk flag never lowers the tier), and every valid risk profile maps to the expected tier and verb in the decision table.
3. A grant token is never returned to the client that proposed the action.
4. Approve is one conditional update inside one transaction; two concurrent approvals of the same token cannot both succeed; a token is unique across all grants.
5. Verb mismatch burns; a wrong caller does not; nobody approves their own proposal.
6. Expired grants are swept on every operation; a lapsed grant is never reported as waiting, and the expiry instant counts as expired.
7. Rate rails are keyed by verb family; a slot is reserved in the staging transaction, approval never charges again, and abort and rejection release it.
8. A grant authorizes one immutable action record. The executor runs only the stored record and refuses if its hash, its policy version, or the resource's current version differs from what was approved.
9. The effect, the grant's move to `EXECUTED`, and the ledger row commit in one transaction or not at all; a retry of an executed grant returns the first outcome and repeats no effect.
10. Hard constraints deny regardless of tier and cannot be unlocked from request content.
11. Every ledger row's hash covers its content and the previous row's hash; `verify()` names the first broken row; the genesis row cannot be silently rewritten when chained rows exist. No ledger row holds caller-supplied text.
12. A memory entry at T4 or above carries exactly one canary for life.
13. The guard is destination-blind: a canary in an outgoing payload aborts even for the owner.
14. A broken guard (any exception while scanning) aborts the response rather than serving it.
15. Exactly the routes listed in `UNGUARDED_ROUTES` skip the guard; a test enumerates every route and fails on a new exemption.
16. Unauthenticated `/mcp` requests receive a 401 with an empty body before any MCP processing; any request with an `Origin` header is refused.
17. Token lookup is keyed by the presented client id, so a token replayed under another id fails.
18. Each client's memory reads are capped at its ceiling; T5 is unreachable over MCP for every identity.
19. `list_pending_approvals`, `approve`, and `abort` are callable only by identities with the `approver` flag, which is a separate flag from "may stage".
20. No response anywhere contains a ready-to-submit approval string.
21. A visitor session sees and changes only its own namespace; one session's identities cannot list, approve, abort, or run another session's grants. The trust core enforces this with a scope on every grant, rail slot, action record, and ledger row (change 001, spec 1.26).
22. The repo contains no string from the private denylist (supplied to CI as a secret; never committed).

## 6. Storage

One SQLite file (`petasos.sqlite`, WAL mode) on one Fly volume, with clear module boundaries: `storage.py` owns connections and transactions, and each package owns its own tables (`trust`: grants and rail slots; `ledger`: the chain; `helpdesk`: tickets, notes, fake mail, fake refunds; `memory`: entries and the canary registry; `sessions`: visitor sessions). One file is what lets an effect, a grant change, and its ledger row commit together (D-016). An earlier draft split this into three files so a grant token never shared a database with tool-readable data; in one process that can open every file, the split bought no security and cost atomicity.

Visitor data (tickets, notes, mail, refunds, memory, grants) lives in the visitor session's namespace and is deleted when the session expires, within the hour. The ledger is kept longer so "verify chain" has history; it can be, because no ledger row holds caller-supplied text (invariant 11). The exact retention, rotation, and reset ordering are specified in change 005.

## 7. Deployment

- Fly.io app `petasos-api`, one `shared-cpu-1x` machine, 256 MB, region closest to Elias, `auto_stop_machines = false`. Health check `GET /healthz` (unauthenticated, the only such route besides `/`).
- Cloudflare: `petasos.io` zone. `api.petasos.io` CNAME to the Fly app, proxied; a Cloudflare rate-limiting rule at the edge (100 requests/minute per IP) on top of the per-token limits in the app. `petasos.io` and `www` served by Cloudflare Pages from `site/` and `demo/dist`.
- GitHub Actions: `ci.yml` (pytest, ruff, vitest, denylist) on every PR and on `main`; `claude-builder.yml` (scheduled, one run at a time) and `claude-mention.yml` (`@claude`); `claude-review.yml` (code-review plugin on builder PRs); `merge-gate.yml` (every 15 minutes, no model: merges a builder PR only when CI, both reviews, the report, and the file wall all pass, D-015); `deploy-api.yml` (flyctl deploy) and `deploy-site.yml` (Pages), started by hand with an exact commit, never by a merge. Codex review is configured in the Codex GitHub integration, not in the repo.
- `main` is protected by a repository ruleset: required checks `python`, `demo`, `private-identifiers`; zero required reviews; linear history; no force push; no deletion.
- Secrets in GitHub: `CLAUDE_CODE_OAUTH_TOKEN`, `FLY_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `PRIVATE_DENYLIST`. There are no published demo tokens: a visitor mints a one-hour session with one documented command (D-017).

## 8. Dependencies (exact pins, `pip check` in CI)

`fastapi`, `uvicorn`, `mcp` (the official SDK; the pin in `pyproject.toml` was resolved against PyPI on 2026-09-26 and change 000 confirms it installs cleanly), `pydantic`, `aiosqlite`, `pytest`, `pytest-asyncio`, `hypothesis`, `httpx`, `ruff`. Nothing else without a decision.

## 9. What is deliberately simpler than Talaria

- No LLM in the request path. The demo tools are called by whatever MCP client the reviewer brings; Petasos never calls a model itself in v1. This is what makes it free to run and deterministic to test.
- One gate generation, not two.
- The ledger chains only the `ledger` table and says so in `ledger/store.py`. `verify()` shows the chain is internally consistent; someone who can rewrite the whole file can recompute it, and the docs say that.
- One rate limiter design from day one (verb-family rails), not three incidents' worth of patches.
- Bearer tokens, not OAuth, with the README naming OAuth 2.1 as the first hardening step.

## 10. Known-trap list for builders (from the Talaria scan)

- Mount `/mcp` as an exact `Route`, not a `Mount`: a Mount redirects the bare path before the identity middleware runs.
- The host app owns the MCP session-manager lifespan; a sub-app's lifespan never executes under Starlette.
- Pin the MCP SDK and its transitive `starlette` exactly; run `pip check`.
- Give uvicorn a graceful-shutdown timeout, and keep a watchdog that exits the process if a hung SSE stream keeps it alive with no listener.
- Never string-patch SQL DDL; write migrations as explicit table rebuilds with a post-migration read-back.
- Inject the clock into anything that computes expiry, and derive test dates from that clock.
- A constructor must never create files or tables as a side effect of a "read-only" probe.
