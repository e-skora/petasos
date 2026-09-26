# Proposal: 002-memory-canary-guard

status: proposed
lane: claude
depends_on: 001-trust-core (uses its `storage.Database`)
decisions: D-001, D-003

## Why

A demo that only gates actions is half the story: private memory must not leak even by accident, and that claim needs to be checkable, not asserted.

## What this change delivers

`petasos.memory`: a tiered entry store (T0 public through T5 private-local, each entry carrying an `expires_at` flag), a canary module that mints one hidden token per T4-or-above entry and never re-mints it, and a destination-blind guard that scans any outgoing payload for planted canaries and aborts on a hit regardless of who is asking. Canaries are a detector with stated limits: they catch a payload that contains the planted marker, not leaks in general; tier filtering in `petasos.trust` is the primary control.

## What this change does not deliver

No MCP server, no help-desk tools, no demo UI. The guard is proven against fixtures and in-process calls only; wiring it to real outgoing responses is change 003.

## Reader test

After this merges, a reader can plant a canary, hand the guard a payload containing it, and watch the guard abort, with no MCP server or demo app needed.
