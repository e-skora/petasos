# ARCHITECTURE.md: how Petasos is built

Status: PROPOSED, version 0.1. Binding once ratified. Live code beats this document; when they disagree, fix one of them in the same PR and bump the version here.

Plain-language rule: every term is defined the first time it appears. If you find one that is not, that is a bug in this document.

## 1. The shape in one picture

```
                 +-----------------------------+
   curl / Claude |  api.petasos.io  (Fly.io)   |
   / Codex ----->|  FastAPI process            |
   (MCP client)  |                             |
                 |  [1] identity middleware    |  401 before any MCP handshake
                 |  [2] MCP server  /mcp       |  tools = help-desk actions
                 |  [3] trust gate             |  manifest -> tier -> execute or stage
                 |  [4] memory + canary guard  |  every response scanned on the way out
                 |  [5] ledger (hash chain)    |  every decision recorded
                 |  [6] owner HTTP endpoints   |  /owner/* for the demo app, never /mcp
                 +-----------------------------+
                          ^          |
   browser ---------------+          v
   petasos.io (Cloudflare Pages)   SQLite files on a Fly volume
   site + demo app (Preact)        (reset hourly)
```

Numbers in brackets are the layers a request passes through, in order.

## 2. Vocabulary

- **MCP**: Model Context Protocol, the open standard an AI assistant uses to discover and call outside tools. A Petasos server is one such tool provider.
- **Manifest**: a small structured record describing what an action *does*: which tool, what it mutates, whether it is reversible, whether it reaches outside the system, whether it touches money or secrets, and so on. It has **no tier field**. A test pins that.
- **Tier (L1 to L5)**: how dangerous an action is. Derived from the manifest by fixed rules. L1 read-only; L2 reversible internal write; L3 logged low-risk automation; L4 external write (needs human approval); L5 money, deletion, secrets, public posting (needs a longer human approval).
- **Memory tier (T0 to T5)**: how private a stored fact is. T0 public through T5 private-local. A different ladder from L1 to L5; they share no code.
- **Grant**: the record of a staged action: verb, single-use token, tier, expiry, approved manifest hash, state.
- **Verb**: a short word bound to the grant (`SEND-EMAIL`, `ISSUE-REFUND`, `DELETE-TICKET`). The human sees the verb; the approval must match it.
- **Client identity**: who is calling. Each identity has a bearer token, a memory-tier ceiling, and an action scope.
- **Canary**: a random token quietly planted inside private memory text. If it ever appears in an outgoing payload, something leaked.
- **Guard**: the function that scans outgoing payloads for canaries and privacy-labelled keys and aborts on a hit.
- **Ledger**: the append-only audit log with a hash chain.

## 3. Package layout

```
src/petasos/
  trust/
    manifest.py      # Manifest dataclass, validation, no tier field
    tiers.py         # derive_tier(manifest) -> Tier: pure, max-over-floors, only ratchets up
    verbs.py         # verb_for(manifest): severity-ordered, every verb has an executor (test)
    grants.py        # GrantStore: stage, approve (compare-and-swap), abort, expire, rate rails
    executor.py      # run(grant): re-verify manifest hash, then call the tool
    constraints.py   # hard constraints above the ladder (deny regardless of tier)
  ledger/
    chain.py         # append(row) with prev_hash; verify() names the first broken row
    store.py         # SQLite schema; documents which tables are chained
  memory/
    store.py         # tiered entries, expires_at flag, per-client read ceiling
    canary.py        # mint, embed once, register; never re-mint
    guard.py         # assert_no_private_payload(payload, canaries): destination-blind
  mcp/
    identity.py      # ClientRegistry: token lookup keyed by presented client id, constant-time compare
    middleware.py    # ASGI: 401 before the MCP handshake; refuse any Origin header
    server.py        # streamable HTTP mount at /mcp as an exact Route (not a Mount)
    tools.py         # help-desk tools; each returns a manifest for the gate
    guard_seam.py    # the ONE place responses are scanned; UNGUARDED_ROUTES enumerated
  explain/           # stretch: verdict -> one plain sentence from facts only
  intent/            # stretch: did the human ask?
  helpdesk/
    data.py          # fictional tickets, hourly reset, the canary ticket
    fake_email.py    # records "sent" mail in a table; never sends
    fake_payments.py # records refunds; never moves money
  owner/
    api.py           # /owner/* endpoints the demo app calls (list approvals, approve, abort, ledger, verify)
  app.py             # composition root: builds everything, mounts routes, owns lifespan
demo/                # Preact + Vite app (served from petasos.io)
site/                # static site
tests/
  test_no_private_identifiers.py   # reads the denylist from the PRIVATE_DENYLIST secret in CI; skips loudly without it
  ...
changes/             # the build ledger (see AGENTS.md)
```

## 4. Request flow for a risky action

1. An MCP client calls `reply_to_customer` with a valid `agent` token.
2. **Identity middleware** authenticates before the MCP layer sees the request. No token, wrong token, or any `Origin` header: bare 401, no tool list, no handshake.
3. The tool builds a **manifest** (external write, not reversible, no money).
4. `derive_tier` returns **L4** by rule. The client's scope allows L1 to L3 directly; L4 and L5 always stage.
5. `GrantStore.stage` creates a grant in state `AWAITING` with verb `SEND-EMAIL`, a URL-safe random token, a 24-hour expiry, and checks the per-hour rail for that verb.
6. The ledger appends a `staged` row (hash-chained).
7. The response to the client is `{"status": "staged", "verb": "SEND-EMAIL", "tier": "L4", "explanation": "..."}`. **No token is returned to the proposer.**
8. The owner (demo app or `owner` token) calls `list_pending_approvals`, sees the grant with its token, and approves by press-and-hold (the app submits the token) or by `approve(token, verb)` over the API.
9. `GrantStore.approve` runs one SQL statement: update the row from `AWAITING` to `APPROVED` where verb, token, and unexpired all match. A partial unique index on `(verb, token) WHERE state='AWAITING'` makes the token single-use without a read-then-write race. Right token, wrong verb: the grant is **burned** (state `REJECTED`, reason `verb_mismatch`). Right verb, wrong client: refused, grant survives.
10. The executor re-hashes the manifest, compares it to the hash stored at staging, and only then calls the tool. Any mismatch refuses and logs.
11. The fake email table gets a row; the ledger appends `executed`; the owner gets a plain-language outcome sentence.

Every response on the way out passes the **guard seam** exactly once.

## 5. Invariants (each one is a test)

1. The manifest type has no tier field.
2. `derive_tier` is pure and monotone: adding any risk flag never lowers the tier.
3. A grant token is never returned to the client that proposed the action.
4. Approve is one compare-and-swap statement; two concurrent approvals of the same token cannot both succeed.
5. Verb mismatch burns; surface mismatch does not.
6. Expired grants are swept on every read; a lapsed grant is never reported as waiting.
7. Rate rails are keyed by verb family, and the same predicate decides the rail on both the staging path and the approval path.
8. The executor refuses if the manifest hash differs from the staged hash.
9. Hard constraints deny regardless of tier and cannot be unlocked from request content.
10. Every ledger row's hash covers its content and the previous row's hash; `verify()` names the first broken row; the genesis row cannot be silently rewritten when chained rows exist.
11. A memory entry at T4 or above carries exactly one canary for life.
12. The guard is destination-blind: a canary in an outgoing payload aborts even for the owner.
13. A broken guard (any exception while scanning) aborts the response rather than serving it.
14. Exactly the routes listed in `UNGUARDED_ROUTES` skip the guard; a test enumerates every route and fails on a new exemption.
15. Unauthenticated `/mcp` requests receive a 401 with an empty body before any MCP processing; any request with an `Origin` header is refused.
16. Token lookup is keyed by the presented client id, so a token replayed under another id fails.
17. Each client's memory reads are capped at its ceiling; T5 is unreachable over MCP for every identity.
18. `list_pending_approvals`, `approve`, and `abort` are callable only by identities with the `approver` flag, which is a separate flag from "may stage".
19. No response anywhere contains a ready-to-submit approval string.
20. The repo contains no string from the private denylist (supplied to CI as a secret; never committed).

## 6. Storage

Three SQLite files on one Fly volume, all WAL mode: `helpdesk.sqlite` (tickets, notes, fake mail, fake refunds), `trust.sqlite` (grants, rate events, ledger), `memory.sqlite` (entries, canary registry). The trust file is separate from everything else on purpose: a grant token never shares a database with data a tool can read. An hourly job rebuilds `helpdesk.sqlite` and `memory.sqlite` from fixtures and truncates grants; the ledger is kept (rotated daily, last 7 days retained) so the "verify chain" demo has history.

## 7. Deployment

- Fly.io app `petasos-api`, one `shared-cpu-1x` machine, 256 MB, region closest to Elias, `auto_stop_machines = false`. Health check `GET /healthz` (unauthenticated, the only such route besides `/`).
- Cloudflare: `petasos.io` zone. `api.petasos.io` CNAME to the Fly app, proxied; a Cloudflare rate-limiting rule at the edge (100 requests/minute per IP) on top of the per-token limits in the app. `petasos.io` and `www` served by Cloudflare Pages from `site/` and `demo/dist`.
- GitHub Actions: `ci.yml` (pytest, ruff, vitest, denylist) on every PR; `deploy-api.yml` (flyctl deploy) and `deploy-site.yml` (Pages) on merge to `main`; `claude-builder.yml` (scheduled + `@claude`); `claude-review.yml` (code-review plugin on PR events). Codex review is configured in the Codex GitHub integration, not in the repo.
- Secrets in GitHub: `CLAUDE_CODE_OAUTH_TOKEN`, `FLY_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `PRIVATE_DENYLIST`. Demo client tokens are not secrets; they are generated at deploy from a seed and printed in the README.

## 8. Dependencies (exact pins, `pip check` in CI)

`fastapi`, `uvicorn`, `mcp` (the official SDK; the pin in `pyproject.toml` was resolved against PyPI on 2026-09-26 and change 000 confirms it installs cleanly), `pydantic`, `aiosqlite`, `pytest`, `pytest-asyncio`, `hypothesis`, `httpx`, `ruff`. Nothing else without a decision.

## 9. What is deliberately simpler than Talaria

- No LLM in the request path. The demo tools are called by whatever MCP client the reviewer brings; Petasos never calls a model itself in v1. This is what makes it free to run and deterministic to test.
- One gate generation, not two.
- The ledger chains every table it writes; where it does not (none in v1), it says so in `ledger/store.py`.
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
