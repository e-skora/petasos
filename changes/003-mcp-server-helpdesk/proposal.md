# Proposal: 003-mcp-server-helpdesk

status: proposed
lane: claude
depends_on: 001-trust-core, 002-memory-canary-guard
decisions: D-001, D-002, D-003

## Why

The trust gate and the memory guard only matter once something real calls them: a live MCP surface lets a reviewer, or their own MCP client, watch the gate work rather than read about it.

## What this change delivers

`petasos.mcp`: identity lookup with a constant-time token compare, ASGI middleware returning 401 before any MCP handshake and refusing any `Origin` header, the server mounted at `/mcp` as an exact route, and the guard seam every response passes through exactly once. `petasos.helpdesk`: fictional tickets, fake email and refund tables that record but never send or move anything, the hidden canary ticket, and the tools in PRODUCT.md section 6.2, each building a manifest for the gate rather than declaring its own tier.

## What this change does not deliver

No owner web UI, no deployment. The owner-only tools (`list_pending_approvals`, `approve`, `abort`) exist here as MCP tools; the demo app that calls them is change 004.

## Reader test

After this merges, a reviewer can point any MCP client at the server with each of the three demo tokens and see three different tool lists and behaviors, per PRODUCT.md section 7.
