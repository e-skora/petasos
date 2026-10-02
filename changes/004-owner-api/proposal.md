# Proposal: 004-owner-api

status: merged
lane: claude
depends_on: 003-mcp-server-helpdesk
decisions: D-002, D-003, D-009, D-016, D-017

## Why

An MCP server proves the gate works to another program; it does not prove it to a human reviewer in five minutes. PRODUCT.md's rung 2 is a small web app a person can watch. That app needs a second front door into the same gate, one a browser can use. This change builds that door; change 007 builds the app on top of it. (This proposal was split from the earlier `004-owner-api-and-demo-app` on 2026-09-30, because one builder run of both halves would not fit the builder's 90 minute limit.)

## What this change delivers

`petasos.owner`: browser endpoints under `/owner/` (never `/mcp`), all calling the same `Gate`, `GrantStore`, `Executor`, and `Ledger` the MCP tools call: who am I, list and read tickets, propose an action, list approvals as cards built from the action record itself, approve, abort, abort everything, view this session's ledger rows in plain language, and verify the chain. The spec names, for every endpoint, its authentication, allowed browser origins, cross-origin handling, which role may call it, its quota family, and its response shape.

The call logic both doors share (argument shape check, role tool sets, the card, approve-then-run) moves into one service module that the MCP adapters and the browser endpoints both import, so the two doors cannot drift.

## What this change does not deliver

No demo app (007), no production deployment (005), no case-study prose (006). The endpoints are exercised by tests through the in-process app; nothing here needs a browser.

## Reader test

After this merges, a person with the three tokens from `POST /session` can, with `curl` alone, list tickets as the visitor, propose the $42.00 refund as the agent and see it staged with no token, list the card as the owner, approve exactly that card, read the refund row on ticket 3, and read the ledger rows for it in plain sentences.
