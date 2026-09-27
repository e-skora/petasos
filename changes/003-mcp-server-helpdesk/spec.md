# Spec: 003-mcp-server-helpdesk

Binding once the proposal is ratified. The builder implements this table; a task that seems to need a different answer is a stop-and-flag.

Version 1, 2026-09-27. Written against `main` at e61705a (000 and 001 merged) and the 002 spec (memory, canaries, guard), which must merge before this change is grounded. Interfaces named from 001 and 002 exist or are specified; nothing here changes them.

## 0. Terms used here

- **Identity**: one authenticated caller: an id, a role (`visitor`, `agent`, or `owner`), a secret, a memory ceiling, a `may_stage` flag, an `approver` flag, and the session (scope) it belongs to.
- **Bearer string**: what a client sends in `Authorization: Bearer <id>.<secret>`. The id before the first dot is the presented client id; the lookup is keyed by it, so a secret replayed under another id fails (ARCHITECTURE.md invariant 17).
- **Visitor session**: three identities (one per role), one scope, one hour of life, its own fictional tickets and memory. Minted by `POST /session`. The scope is the session id and is the `scope` every 001 and 002 store is built with.
- **Guard seam**: the one ASGI layer through which every HTTP response passes on its way out, calling the 002 guard on each response body. Only the routes in `UNGUARDED_ROUTES` skip it.
- **Adapter**: an MCP tool whose body only maps the call to `Gate.propose` (or, for owner tools, to `GrantStore` and `Executor`) and maps the `Result` to JSON. Adapters never compute risk, tier, verb, or approver.
- **Canary ticket**: one hidden ticket per session whose body is stored as a T4 memory entry, so it carries a canary. It never appears in listings; fetching it by id returns the canary in the body, the seam trips, and the response is stopped.
- **Global ceiling**: an abuse counter across all sessions that a data reset never clears.

## 1. Decisions for this change

Identity and middleware

| # | Decision | Choice | Why |
|---|---|---|---|
| 3.1 | Identity record | `Identity(id: str, role: Literal["visitor","agent","owner"], scope: str, ceiling: MemoryTier, may_stage: bool, approver: bool, expires_at: datetime)`, frozen, in `mcp/identity.py`. Role fixes the flags: `visitor` (T0, `may_stage=False`, `approver=False`), `agent` (T3, `may_stage=True`, `approver=False`), `owner` (T4, `may_stage=True`, `approver=True`). No identity has ceiling T5. | PRODUCT.md section 6.2; invariant 18 (T5 unreachable over MCP); invariant 19 (`approver` separate from `may_stage`). |
| 3.2 | Registry | `ClientRegistry(db)` with `authenticate(bearer: str, *, now: datetime) -> Identity \| None`. Splits at the first `.` into `(client_id, secret)`; looks up the row for `client_id` in `identities`; compares `sha256(secret)` against the stored digest with `hmac.compare_digest`; refuses when the row is missing, the digest differs, or `expires_at <= now`. A missing row still runs one `compare_digest` against a fixed dummy digest, so timing does not reveal whether the id exists. | Invariant 17; constant-time compare keyed by the presented id. |
| 3.3 | Middleware | `IdentityMiddleware(app, registry, clock)` replaces the 000 token map. For `/mcp` and `/mcp/`: any `Origin` header, a missing or malformed `Authorization`, or `authenticate` returning `None` gives an empty 401 before the MCP layer runs (unchanged shape from 000). On success it sets `scope["state"]["identity"]` to the `Identity` for this one HTTP request. Identity is per request: an MCP session id is never an identity. The middleware records the `Mcp-Session-Id` header of each initialize response against the identity id that made it (in memory, bounded to 10,000 entries, oldest dropped) and refuses with 401 any later request that carries that session id under a different identity. `/session` and `/healthz` pass through unauthenticated. | Invariant 16; the streamable transport carries a session id that must not become a credential. |
| 3.4 | Tool visibility | A `ServerMiddleware` on the `MCPServer` (the pinned `mcp` 2.2.0 `mcp.server.context.ServerMiddleware`, `(ctx, call_next)`) reads the identity from `ctx.request` and: for `tools/list`, drops every tool not in the identity's tool set; for `tools/call`, refuses a tool outside that set with `refused/not_allowed` before any adapter runs. Tool sets: `visitor`: `list_tickets`, `get_ticket`, `recall`; `agent`: those plus `add_internal_note`, `reply_to_customer`, `issue_refund`, `delete_ticket`; `owner`: the agent's set plus `list_pending_approvals`, `approve`, `abort` (PRODUCT.md section 6.2's owner surface; `abort_all` stays in the trust core for 004's owner API and is not an MCP tool). `ping` stays, visible to all. | PRODUCT.md success criterion: three tokens, three tool lists. Invariant 19. |

The MCP surface

| # | Decision | Choice | Why |
|---|---|---|---|
| 3.5 | Adapters | Every help-desk tool is an MCP tool in `mcp/tools.py` whose body is `gate.propose(name, arguments, proposer=identity.id)` on the `Gate` for the identity's scope, then `result_json(result)`. `recall` reads `MemoryStore(db, scope=identity.scope, clock=clock).search(query, ceiling=identity.ceiling)`. No adapter has a parameter that carries risk, tier, verb, approver, or scope; a test reflects on every adapter signature (acceptance test 8). | 001 spec 1.2; invariant 1. |
| 3.6 | Response shape | `result_json(result) -> dict`: `{"status": code, "explanation": sentence, "tier": "L5" or null, "verb": "ISSUE-REFUND" or null, "grant_id": int or null}`. Never a token, never the record. Read tools return their data under `"data"` with the same `status` key (`"ok"`). The owner's `list_pending_approvals` returns `{"status": "ok", "data": [card, ...]}` where a card is `{"grant_id", "token", "verb", "tier", "record": {...}, "expires_at"}` from `Grant.for_approver()`; it is the only response that ever carries a token, and only an approver can call it. | Invariant 3 and 20 for proposers; 001 spec 1.21 for the approver's card. |
| 3.7 | Owner tools | `list_pending_approvals()` -> `gate.grants.list_pending(viewer=identity.id)` rendered as cards. `approve(token: str, verb: str)` -> `gate.grants.approve(token=, verb=, approver=identity.id)`; when the result is `approved`, the adapter then calls `gate.executor.run(result.grant_id)` and returns that run's result (`executed`, or the refusal). `abort(token: str)` maps to `GrantStore.abort`. All three are refused with `refused/not_allowed` for any identity without the `approver` flag, before reaching the store (belt and braces: the store refuses again with `refused/not_an_approver`). | ARCHITECTURE.md section 4 steps 8 to 11; D-009 (the client submits the token; nothing composes a phrase). |
| 3.8 | Gates per scope | `GateFactory(db, registry_of_tools, clock)` builds `Gate(db, tools, approvers=frozenset({owner id of that scope}), scope=session id, clock)` per request from the identity; nothing caches a `Gate` across sessions. | 001 spec 1.26: one visitor's owner never sees another's grants. |
| 3.9 | Guard seam | `mcp/guard_seam.py`: `GuardSeam(app, db)` is raw ASGI middleware installed outermost, so it sees every response of every route. `UNGUARDED_ROUTES = frozenset({"/healthz"})`; a test enumerates every route the app registers and fails if any route outside that set is not wrapped (invariant 15). For each `http.response.body` chunk it holds back the chunk, scans it as `bytes` against `all_canaries(conn)` (read once per response, on the first chunk) with a carried tail of 24 bytes (one less than a token) so a token split across chunks is still found, and, when the response `content-type` is `application/json`, parses the complete body and scans it as a structure (private keys). For `text/event-stream` responses it scans each event's `data:` line as bytes and, when it parses as JSON, as a structure. | Invariant 13 and 15; canaries are contiguous tokens, so a byte scan with a tail catches them in a stream. |
| 3.10 | On a hit | If the response headers have not been sent: send `409` with body `{"status": "refused/guard_tripped", "explanation": <the Hit sentence>}` and stop. If headers were already sent (a stream): send an SSE event with that JSON, then close the body with `more_body: False`; the offending chunk is never forwarded. In both cases append one ledger row in its own transaction: `kind="refused"`, `actor=identity.id` (or `"anonymous"`), `scope=identity.scope` (or `"none"`), `detail={"code": "guard/canary" or "guard/private_key" or "guard/error", "route": path}`; never the token or the key. Any exception inside the seam, including a failed ledger write, is itself a hit of kind `guard_error` and stops the response. | Invariant 14: a broken guard aborts rather than serves. PRODUCT.md: fetching the canary ticket writes a ledger row. |

The help desk

| # | Decision | Choice | Why |
|---|---|---|---|
| 3.11 | Tables | `helpdesk/data.py`: `tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, number INTEGER NOT NULL, customer TEXT NOT NULL, email TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, amount_minor INTEGER, currency TEXT, version INTEGER NOT NULL, hidden INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(scope, number))`, `notes(id, scope, ticket_id, author, text, created_at)`, `fake_mail(id, scope, ticket_id, to_address, subject, body, grant_key TEXT NOT NULL UNIQUE, sent_at)`, `fake_refunds(id, scope, ticket_id, amount_minor, currency, grant_key TEXT NOT NULL UNIQUE, refunded_at)`. `HELPDESK_SCHEMA`; `Database.migrate()` imports it like the others. | ARCHITECTURE.md section 6: each package owns its tables. |
| 3.12 | Resource versions | Every write to a ticket (note added, reply sent, refund issued, delete) bumps `tickets.version` by one inside the effect's transaction. `current_version(conn, "ticket:<number>")` reads it. Tools resolve to `resource="ticket:<number>"` with that version. | 001 spec 1.6, 1.16: an approval of version 3 never executes against version 4. |
| 3.13 | Seed data | `seed_session(conn, db, scope, clock)` runs inside the caller's transaction (no `BEGIN` of its own; `SessionStore.mint` opens the one transaction that writes the session, its identities, and the seed together) and inserts six visible tickets with invented customers and obviously fictional emails at `example.invalid`, numbered 1 to 6: at least one open ticket with `amount_minor=4200, currency="USD"` (the refund demo), one already resolved, one with a note; plus the canary ticket, number 7, `hidden=1`, whose `body` is the text returned by `MemoryStore(db, scope=scope, clock=clock).put_in(conn, "ticket-7.body", <private text>, MemoryTier.T4)` (002 spec 2.4a: the same connection and transaction as the ticket rows, so it holds the canary and commits with them) and whose subject is neutral. It also puts (with `put_in`) four memory entries at T0, T1, T3, T4 so `recall` returns three different sets for the three ceilings (T0 alone; T0, T1, T3; all four). All seed text is in a constant table in `data.py`; no real name, company, hostname, or address. | PRODUCT.md section 6.2 (one hidden canary ticket; three different memory result sets); hygiene rule 1. |
| 3.14 | Tool definitions | `helpdesk/tools.py` builds the `ToolRegistry`: `list_tickets` and `get_ticket` (`mutation="none"`, reversible; L1, `READ`); `add_internal_note` (`internal`, reversible; L2, `ADD-NOTE`); `reply_to_customer` (`external`, not reversible, no money, no delete; L4, `SEND-EMAIL`); `issue_refund` (`external`, not reversible, `touches_money`; L5, `ISSUE-REFUND`); `delete_ticket` (`internal`, not reversible, `deletes`; L5, `DELETE-TICKET`). A test asserts each derived (tier, verb) pair against this table. `recall` is not a `ToolDefinition`: it is a read through `MemoryStore` with the identity's ceiling and runs no effect. | PRODUCT.md tool table; 001 spec 1.3, 1.4. |
| 3.15 | Validation | `validate` per tool returns the exact argument set: `list_tickets` `{}` (arguments must be empty); `get_ticket` `{"ticket": int >= 1}`; `add_internal_note` `{"ticket": int, "text": str 1..2000}`; `reply_to_customer` `{"ticket": int, "body": str 1..4000}`; `issue_refund` `{"ticket": int, "amount": str matching `^\d{1,7}\.\d{2}$`, "currency": "USD"}` (v1 accepts USD only); `delete_ticket` `{"ticket": int}`. Unknown keys, wrong types, out-of-range values, a float anywhere, or `amount` given as a number rather than a string raise `InvalidArguments`. | 001 spec 1.19; amounts are strings on the wire so `42.00` never becomes a float. |
| 3.16 | Resolution | `resolve` reads the ticket in the caller's scope by number: missing or `deleted=1` raises `NotFound`; for every tool except `get_ticket`, `hidden=1` raises `NotFound` too (the canary ticket cannot be acted on, only read). `reply_to_customer` resolves `destination=<ticket email>`; `issue_refund` resolves `destination=<ticket email>`, `amount_minor=int(amount without the dot)`, `currency="USD"`, and raises `InvalidArguments` if `amount_minor` exceeds the ticket's `amount_minor` (no over-refund) or the ticket already has a refund. `get_ticket` of the canary ticket resolves and executes like any read: it returns the row including the body, and the seam trips (3.9). | Invariants 8 and 10; the canary ticket must be readable by id or there is nothing to demonstrate. |
| 3.17 | Effects | Each effect runs on the connection it is given and is keyed by the grant id (the idempotency key from 001 spec 1.16): `reply_to_customer` inserts one `fake_mail` row with `grant_key=<key>` (UNIQUE, so a retry inserts nothing new and returns `executed`); `issue_refund` inserts one `fake_refunds` row the same way; `add_internal_note` inserts a note; `delete_ticket` sets `deleted=1`. Each bumps the ticket version and returns the outcome code `"executed"`. Nothing sends mail or moves money, and the module docstrings say so. | Proposal: "record but never send or move anything, each effect keyed by the grant id". |
| 3.18 | Reads | `list_tickets` returns visible (`hidden=0`, `deleted=0`) tickets of the scope: number, customer, subject, status, amount, currency, version; never bodies. `get_ticket` returns one ticket with body, notes, mail sent, and refunds, by number in the scope; a hidden ticket is returned too (that is the trap); a deleted one is `NotFound`. Both run through `Gate.propose` as L1 direct actions so every read is ledgered (`kind="executed"`, verb `READ`). | ARCHITECTURE.md section 4: one code path for every effect, reads included. |

Sessions and ceilings

| # | Decision | Choice | Why |
|---|---|---|---|
| 3.19 | Mint | `POST /session` (no auth, no body) in `sessions/visitor.py`: creates `sessions(id TEXT PRIMARY KEY, created_at, expires_at)` with `id = "s-" + token_urlsafe(12)` and `expires_at = now + 1 hour`; three `identities(id TEXT PRIMARY KEY, session_id, role, secret_digest, ceiling, expires_at)` rows with ids `<session>-visitor`, `<session>-agent`, `<session>-owner` and secrets `token_urlsafe(24)`; runs `seed_session`; returns `201` `{"session": id, "expires_at": iso, "mcp_url": "/mcp", "tokens": {"visitor": "<id>.<secret>", "agent": ..., "owner": ...}, "note": "The owner token is a simulation of approval for this demo, not proof that a person approved."}`. Secrets are returned once and stored only as digests. | D-017; PRODUCT.md: the owner identity is a simulation role and the docs say so. |
| 3.20 | Expiry and deletion | An identity past `expires_at` fails `authenticate` (401). `SessionStore.expire(now)` deletes every row of an expired session's scope from `tickets`, `notes`, `fake_mail`, `fake_refunds`, `memory_entries`, `grants`, `rail_slots`, `identities`, and `sessions`, in one transaction, in that order; `canaries` and `ledger` rows are kept (002 spec 2.7; 001 spec 1.22). It runs at the start of every `POST /session`. Scheduling it on a timer, retention, and the reset ordering are 005's. | ARCHITECTURE.md section 6. |
| 3.21 | Per-session quota | `quota(scope, family, limit, window)` in `sessions/quotas.py` over a `quota_events(scope, family, at)` table: each session may make at most 120 tool calls per rolling hour (family `calls`) and 30 `POST /session` mints are allowed per rolling hour globally (family `mint`, scope `"global"`). Over the limit: MCP `tools/call` returns `refused/quota` (the `ServerMiddleware` checks it before the adapter); `POST /session` returns `429` with `{"status": "refused/quota"}`. Rails from 001 stay per scope on top of this. | PRODUCT.md demo hygiene; D-017. |
| 3.22 | Global ceilings | `abuse_counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL)` holds lifetime counters `sessions_minted`, `tool_calls`, `guard_trips`, incremented in the same transactions as the events they count; ceilings `MAX_LIVE_SESSIONS = 200` (live rows in `sessions`) and `MAX_SESSIONS_PER_DAY = 500` (a `quota_events` family with a 24-hour window, scope `"global"`). `POST /session` returns `503` `{"status": "refused/ceiling"}` above either. No function in this change deletes from `abuse_counters` or the global `quota_events`; 005's reset must not either, and a test here asserts `SessionStore.expire` leaves both untouched. | PRODUCT.md: global abuse ceilings that a data reset never clears. |
| 3.23 | App | `create_app(db: Database, *, clock: Callable[[], datetime]) -> FastAPI` replaces `create_app(tokens)`. Order of middleware, outermost first: `GuardSeam`, `IdentityMiddleware`. Routes: `GET /healthz` (unguarded, unauthenticated), `POST /session`, `/mcp` and `/mcp/` as exact routes (unchanged). `main` in `app.py` reads `PETASOS_DB` from the environment only inside `if __name__ == "__main__"`; nothing at import or in `create_app` reads the environment. `db.migrate()` is the caller's job before `create_app`. | 000's shape; ARCHITECTURE.md section 10. |
| 3.24 | Result codes added here | `refused/not_allowed` ("That tool is not available to this identity."), `refused/quota` ("This session has used its hourly allowance; try again later."), `refused/ceiling` ("The demo is at capacity; try again later."), `refused/guard_tripped` (the 002 Hit sentence), `ok` (reads). They live in `mcp/outcomes.py` in this change, not in `trust/outcomes.py`; a test asserts each has a sentence with no code, id, or underscore. | 001's sentence table is 001's wall. |
| 3.25 | Ledger rows from this change | Only guard trips (3.10). Session mints are counted in `abuse_counters`, not ledgered (the 001 `kind` list has no fitting kind and is not this change's to extend). Tool calls are ledgered by the trust core. No row holds a token, a secret, a customer name, or a ticket body. Acceptance test 24 scans every row. | 001 spec 1.22. |
| 3.26 | Clock and purity | Everything takes `clock`; importing `petasos.mcp`, `petasos.helpdesk`, and `petasos.sessions` has no side effects; no module reads the environment except `app.main`. | 001 spec 1.24; ARCHITECTURE.md section 10. |

Result codes added by this change (complete list): `ok`, `refused/not_allowed`, `refused/quota`, `refused/ceiling`, `refused/guard_tripped`. Every 001 code can also appear in an adapter's response unchanged.

## 2. Public interface

```python
from petasos.storage import Database
from petasos.app import create_app
from petasos.sessions import SessionStore
from petasos.mcp.guard_seam import UNGUARDED_ROUTES

db = Database(tmp_path / "petasos.sqlite")
db.migrate()  # ledger, grants, rail slots, memory, helpdesk, sessions, quotas, counters
app = create_app(db, clock=clock)

# A visitor mints a session (the README's one command in 006 is: curl -X POST https://api.petasos.io/session)
r = client.post("/session")  # 201: tokens for visitor, agent, owner (the owner is a simulation)

# As the agent, over MCP with Authorization: Bearer <agent token>:
#   tools/list  -> ping, list_tickets, get_ticket, recall, add_internal_note, reply_to_customer, issue_refund, delete_ticket
#   issue_refund {"ticket": 3, "amount": "42.00", "currency": "USD"}
#     -> {"status": "staged", "tier": "L5", "verb": "ISSUE-REFUND", "grant_id": 1, "explanation": "..."}   (no token)
#   list_pending_approvals -> {"status": "refused/not_allowed", ...}
# As the owner:
#   list_pending_approvals -> {"status": "ok", "data": [{"grant_id": 1, "token": "...", "verb": "ISSUE-REFUND", "record": {...}}]}
#   approve {"token": "...", "verb": "ISSUE-REFUND"} -> {"status": "executed", ...}  (fake_refunds has one row)
#   approve the same token again -> {"status": "refused/unknown_or_used_or_expired", ...}
# As anyone:
#   get_ticket {"ticket": 7} -> 409 {"status": "refused/guard_tripped", ...} from the seam, and one ledger row
```

`petasos.mcp.__init__` exports `create_mcp_server`, `IdentityMiddleware`, `ClientRegistry`, `Identity`, `GuardSeam`, `UNGUARDED_ROUTES`. `petasos.helpdesk.__init__` exports `HELPDESK_SCHEMA`, `build_registry`, `seed_session`. `petasos.sessions.__init__` exports `SESSIONS_SCHEMA`, `SessionStore`, `session_routes`.

## 3. Acceptance tests (plain language; each becomes a test named for it)

Every test builds its own app on a `tmp_path` database with the frozen clock and drives it through Starlette's `TestClient` or the `mcp` client over streamable HTTP, as `tests/test_app_mcp.py` does today.

Identity and middleware

1. `POST /mcp` with no token, with a malformed bearer (`no-dot`, `.`, `id.`), with a wrong secret, with the right secret under another session's client id, with an expired identity, and with a valid token plus any `Origin` header: each returns 401 with an empty body, and the MCP ASGI app was never called.
2. Three MCP sessions initialized with the visitor, agent, and owner tokens of one session list exactly the tool sets in 3.4, and `ping` works for all three.
3. A request on an MCP session initialized with the agent token but sent with the owner token is refused with 401.
4. `authenticate` runs `compare_digest` exactly once for a missing id and once for a present id (patch it and count); a missing id and a wrong secret both return `None`.

Adapters and responses

5. `issue_refund` as the agent returns `staged`, tier `L5`, verb `ISSUE-REFUND`, a `grant_id`, and no key named `token` anywhere in the response (deep scan); `list_pending_approvals` as the agent is `refused/not_allowed`; as the owner it shows the grant with its token and a record whose `amount_minor` is 4200 and `currency` is `USD`.
6. Owner `approve` with the right token and verb returns `executed`; `fake_refunds` has one row for the grant, the ticket's version rose by one, and the grant is `EXECUTED`. The same `approve` again is `refused/unknown_or_used_or_expired` and `fake_refunds` still has one row.
7. Right token, wrong verb, burns the grant (`refused/verb_mismatch`, then the right verb is refused too); `approve` from the agent is `refused/not_allowed` and the grant survives; the owner cannot approve a grant the owner proposed (`refused/self_approval`).
8. No adapter signature has a parameter whose name contains risk, tier, verb, flag, approver, or scope (reflection over every registered tool); `create_app`'s signature has no `tokens`.
9. `reply_to_customer` as the agent stages an L4 `SEND-EMAIL`; six in one hour: the sixth is `refused/rail_full`. `add_internal_note` executes at once (`executed`, L2) and bumps the version; `delete_ticket` stages an L5 `DELETE-TICKET`; `list_tickets` and `get_ticket` return `ok` with data and each writes an `executed` ledger row with verb `READ`.
10. `issue_refund` with `amount` `"4200"` (no decimals), `42.0`, `42.00` as a JSON number, `"42.001"`, a currency other than `USD`, an amount above the ticket's, an unknown ticket number, ticket 7 (hidden), and a deleted ticket: each is `refused/invalid_arguments` (or `refused/unknown_tool` for a misspelled tool name), with a `refused` ledger row and no grant.
11. After `issue_refund` is staged, a note is added to the same ticket (version bumps); the owner's `approve` then returns `refused/stale_resource`, the grant is `REJECTED`, and no refund row exists.

Guard seam

12. `get_ticket {"ticket": 7}` as visitor, agent, and owner each returns the 409 guard response with `refused/guard_tripped` and the plain sentence; the body contains no `cn-` token; one `refused` ledger row per call with `detail.code == "guard/canary"`.
13. Ticket 7 never appears in `list_tickets` for any identity, and `recall` as the owner (ceiling T4) returns the T4 entry, which then trips the seam on the way out (409), while `recall` as the agent (T3) returns the T0, T1, and T3 entries and passes.
14. `UNGUARDED_ROUTES == {"/healthz"}`; a test enumerates `app.routes` and asserts every other route is served through `GuardSeam` (a marker the seam sets on the response, or a spy on the seam counting one call per request), and `GET /healthz` still returns `{"ok": true}` with the guard patched to raise.
15. With `scan` patched to raise `RuntimeError`, every `/mcp` and `/session` response is the 409 guard response with `detail.code == "guard/error"` in the ledger; nothing else is served.
16. A canary token split across two `http.response.body` chunks (a test ASGI app that sends the first 10 bytes, then the rest) is still caught; a JSON response whose body holds a key `private_notes` is caught as `guard/private_key`; an SSE event whose `data:` holds a canary is caught and the stream ends with the guard event.

Sessions, quotas, ceilings

17. `POST /session` returns 201 with three distinct tokens of the form `<id>.<secret>`, an `expires_at` one hour after the clock, the simulation note, and no secret stored in plain text (the `identities` table holds digests that differ from the returned secrets). Two sessions get six visible tickets each and cannot read each other's: ticket 3 of session A fetched with session B's owner token is `refused/invalid_arguments`.
18. With the clock advanced 61 minutes, every token of the session is refused (401), and the next `POST /session` deletes that session's tickets, notes, mail, refunds, memory entries, grants, rail slots, identities, and session row, while its `canaries` and `ledger` rows remain.
19. The 121st tool call in one hour from one session is `refused/quota`; a call from another session succeeds; 61 minutes later the first session's calls succeed again.
20. The 31st `POST /session` in one hour is 429 `refused/quota`; with `MAX_LIVE_SESSIONS` patched to 2, the third mint while two sessions are live is 503 `refused/ceiling`; expiring one lets the next mint succeed.
21. `abuse_counters` rises by one per mint, per tool call, and per guard trip, and neither `SessionStore.expire` nor any function in `petasos.sessions` or `petasos.helpdesk` lowers any counter or deletes global `quota_events` (a test greps the package for `DELETE FROM abuse_counters` and `DELETE FROM quota_events` too).
22. One session's owner cannot list, approve, or abort another session's grant: with a grant staged in session A, session B's owner sees an empty `list_pending_approvals`, and `approve` with A's token from B is `refused/unknown_or_used_or_expired`.

Plumbing

23. Every derived (tier, verb) of the six help-desk tools matches the table in 3.14; every result code in 3.24 has a sentence with no code, id, or underscore.
24. No ledger row written during the whole suite contains any token, secret, customer name, email, subject, or body used by the seed or the tests (the test scans every row for those strings).
25. Importing `petasos.mcp`, `petasos.helpdesk`, and `petasos.sessions` in a subprocess with an empty environment touches no file, environment variable, or network; `Database.migrate()` creates every table this change adds, and `Database.migrate()` twice is a no-op.

Reader test

26. From a fresh app: mint a session; connect an MCP client with each of the three tokens and see three different tool lists; as the agent propose a $42.00 refund on the seeded refund ticket and get `staged` with no token; as the owner list the card, approve exactly that token with `ISSUE-REFUND`, and get `executed`; `Ledger(db).verify()` is `None`; `fake_refunds` holds one row of 4200 USD and no other refund exists.
