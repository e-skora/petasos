# Proposal: 005-deploy-fly-cloudflare

status: ratified
lane: claude
depends_on: 003-mcp-server-helpdesk, 004-owner-api
decisions: D-004, D-015, D-016, D-017

## Why

Rung 3 of PRODUCT.md's reviewer ladder, connecting from the reviewer's own client, only works if the server is actually running somewhere public. This change makes the API live.

## What this change delivers

The API deployed to Fly.io at `api.petasos.io` (one `shared-cpu-1x` machine, `auto_stop_machines = false`, health check at `/healthz`), and the `deploy-api.yml` workflow, started by hand with an exact commit, never on merge (D-015). After each deploy, the health check and one full refund journey (propose, approve, execute) are run by hand to confirm the release. A documented rollback: redeploy the prior known-good commit by hand and confirm the same health check and refund journey again. Also delivered: the Cloudflare DNS proxy in front of the API with an edge rate-limiting rule.

This change also owns the demo's launch controls: request and response size bounds at the edge and the app, deletion of a visitor session's data within the hour it expires (D-017), the ledger's retention and rotation schedule, and the order operations run in on reset, so a partial reset never leaves the demo in a broken state.

## What this change does not deliver

No demo app deployment (that is `deploy-site.yml`, exercised once `demo/` exists from change 004) and no case-study content.

## Reader test

After this merges, Elias can start a deploy by hand with an exact commit, watch `curl https://api.petasos.io/healthz` return healthy and a full refund journey succeed, and a fresh MCP client can mint a visitor session and connect to `https://api.petasos.io/mcp` with its token.
