# Proposal: 004-owner-api-and-demo-app

status: proposed
lane: codex or claude
depends_on: 003-mcp-server-helpdesk
decisions: D-002, D-003, D-009, D-014, D-016, D-017

## Why

An MCP server proves the gate works to another program; it does not prove it to a human reviewer in five minutes. This change gives Petasos rung 2 of PRODUCT.md's reviewer ladder: a small web app a person can watch.

## What this change delivers

`petasos.owner`: browser endpoints (never `/mcp`) for the demo app, all calling the same `Gate`, `GrantStore`, and `Executor` the MCP tools call: mint a visitor session, list and read tickets, propose an action, list approvals (the card is built from the action record itself), approve, abort, view the ledger, and verify its chain. The spec names, for every endpoint, its authentication, allowed browser origins, cross-origin handling, and which role may call it.

A Preact and Vite demo app under `demo/`, led by a guided scenario: an "Ask for a $42 refund" button that has the simulated agent propose the refund, then shows why it waits, the exact approval card (amount, currency, customer, ticket version), the press-and-hold approval, the fake result, and the new ledger row. Also: the ticket list, an approvals inbox with press-and-hold (1.2 s for L4, 2.5 s for L5, per D-009), a quiet "N waiting for you" pill that is chrome on every screen, a ledger viewer with a "verify chain" button whose result says "the chain is internally consistent", and a "trip the canary" button, styled per D-014's semantic palette.

Keyboard and accessibility: holding the space bar on the focused approve control works like holding the mouse; releasing early, losing focus, or a repeated key event cancels the hold; a plain approve button is offered wherever a hold is not usable (D-009 already allows a button). The role switcher is labelled as a simulation.

## What this change does not deliver

No production deployment (005) and no case-study prose (006). The app runs against a local server for this change's tests.

## Reader test

After this merges, a person with no instructions can open the demo app locally, press "Ask for a $42 refund", read exactly what they are approving, approve it with a hold or the keyboard, and see the fake refund and its ledger row.
