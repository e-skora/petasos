# Proposal: 005-deploy-fly-cloudflare

status: proposed
lane: claude
depends_on: 003-mcp-server-helpdesk
decisions: D-004

## Why

Rung 3 of PRODUCT.md's reviewer ladder, connecting from the reviewer's own client, only works if the server is actually running somewhere public. This change makes the API live.

## What this change delivers

The API deployed to Fly.io at `api.petasos.io` (one `shared-cpu-1x` machine, `auto_stop_machines = false`, health check at `/healthz`), the `deploy-api.yml` workflow exercised for real on a merge to `main`, the Cloudflare DNS proxy in front of it with an edge rate-limiting rule, and the hourly job that rebuilds the fake help-desk and memory data and truncates grants while keeping the ledger, per ARCHITECTURE.md section 6.

## What this change does not deliver

No demo app deployment (that is `deploy-site.yml`, exercised once `demo/` exists from change 004) and no case-study content.

## Reader test

After this merges, `curl https://api.petasos.io/healthz` returns healthy, and a fresh MCP client can connect to `https://api.petasos.io/mcp` with a published demo token.
