# Proposal: 004-owner-api-and-demo-app

status: proposed
lane: codex or claude
depends_on: 003-mcp-server-helpdesk
decisions: D-002, D-003, D-009, D-014

## Why

An MCP server proves the gate works to another program; it does not prove it to a human reviewer in five minutes. This change gives Petasos rung 2 of PRODUCT.md's reviewer ladder: a small web app a person can watch.

## What this change delivers

`petasos.owner`: HTTP endpoints (never `/mcp`) the demo app calls to list approvals, approve, abort, view the ledger, and verify its chain. A Preact and Vite demo app under `demo/`: the ticket list, an approvals inbox with press-and-hold (1.2s for L4, 2.5s for L5, per D-009), a quiet "N waiting for you" pill that is chrome on every screen, a ledger viewer with a "verify chain" button, and a "trip the canary" button, styled per D-014's semantic palette.

## What this change does not deliver

No production deployment (change 005) and no case-study prose (change 006). The app runs against a local server for this change's tests.

## Reader test

After this merges, Elias can run the server and demo app locally, approve a staged email with a press-and-hold, and watch the ledger viewer show the new row.
