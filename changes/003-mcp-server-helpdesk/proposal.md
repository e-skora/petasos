# Proposal: 003-mcp-server-helpdesk

status: ratified
lane: claude
depends_on: 001-trust-core, 002-memory-canary-guard
decisions: D-002, D-003, D-016, D-017

## Why

The trust gate and the memory guard only matter once something real calls them: a live MCP surface lets a reviewer, or their own MCP client, watch the gate work rather than read about it.

## What this change delivers

`petasos.mcp`: identity lookup with a constant-time token compare, ASGI middleware returning 401 before any MCP handshake and refusing any `Origin` header, the server at `/mcp` as an exact route (extending the `ping` server proven in 000), and the guard seam every response passes through exactly once. MCP tools are thin adapters over `Gate.propose`; they never build risk facts.

`petasos.helpdesk`: fictional tickets that carry a resource version bumped on every change, fake email and refund tables that record but never send or move anything (each effect keyed by the grant id so a retry cannot repeat it), the hidden canary ticket, and the help-desk tool definitions (`list_tickets`, `get_ticket`, `add_internal_note`, `reply_to_customer`, `issue_refund`, `delete_ticket`) whose risk profiles, validation, and resolution live on the server.

`petasos.sessions` (D-017): `POST /session` mints a visitor session: three short-lived identities (visitor, agent, owner), a private data namespace seeded with fresh fictional tickets, and a one-hour life. Every read and write is scoped to the caller's namespace, so one visitor can never see, approve, or abort another's work. Per-session quotas sit under global abuse ceilings that a data reset never clears. The public owner identity is a simulation role, and the docs say so: it shows how approval works; it does not prove a human approved.

## What this change does not deliver

No browser endpoints or UI (004), no deployment (005). The owner-only MCP tools (`list_pending_approvals`, `approve`, `abort`) exist here and are visible only to an identity with the `approver` flag; the `agent` identity never sees them.

## Reader test

After this merges, a reviewer can mint a visitor session with one command, point any MCP client at the server with each of its three tokens, see three different tool lists, propose a $42 refund as the agent, and approve exactly that refund as the owner.
