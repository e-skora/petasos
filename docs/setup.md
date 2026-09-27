# Setup

This document lists every one-time step behind the running project: what the planning
thread already did, what only Elias can do, and how to connect an MCP (Model Context
Protocol) client once a server is running. MCP is the open standard an AI assistant
such as Claude or Codex uses to call outside tools; a Petasos server is one such tool
provider.

This build had no live network access to re-check the vendor links below. They are
accurate as of 2026-09-27 to the best of the builder's knowledge; if a link has moved,
search the vendor's current documentation site for the same topic before following it.

## Part 1: already done by the planning thread

- **The repository.** A public GitHub repository, `e-skora/petasos` (DECISIONS.md D-005).
- **The `main` ruleset.** Required checks `python`, `demo`, and `private-identifiers`;
  zero required human reviews; linear history; no force push; no deletion.
- **The merge gate.** `.github/workflows/merge-gate.yml` (DECISIONS.md D-015): a scheduled
  workflow, no model in it, that checks a pull request titled `[build] NNN-slug` every
  15 minutes. It merges that pull request only when its branch contains the current
  `main`, the required CI checks are green on its current head commit, both the Claude
  review and the Codex review of that head commit found no blocker, its `report.md` says
  `Verdict: BUILT` with no unfinished placeholders, and the file wall holds. Anything else
  waits; a pull request that waits through two builder runs with an unresolved problem is
  repaired automatically, at most twice, before it becomes a draft with a question on
  record.

## Part 2: one-time steps only Elias can do

1. **Install the Claude GitHub App.** In a terminal with Claude Code installed, run
   `/install-github-app` and follow the prompts to authorize it on `e-skora/petasos`.
   Checked against the Claude Code GitHub Actions guide,
   https://docs.claude.com/en/docs/claude-code/github-actions (2026-09-27).

2. **Mint the builder's OAuth token.** In the same terminal, run `claude setup-token`.
   Copy the value it prints, then on github.com go to the repository's Settings, then
   Secrets and variables, then Actions, and add it as a repository secret named
   `CLAUDE_CODE_OAUTH_TOKEN`. Same guide as step 1.

3. **Add the private denylist.** On github.com, under the same Secrets and variables
   page, add a secret named `PRIVATE_DENYLIST` (skip this step if it is already set).
   Its value is one denylist entry per line: the private project's hostnames, its GitHub
   path, service labels, secret-store slot names, its historical canary prefix, its
   client header name, its environment variable prefix, home-directory paths, and the
   names of people in Elias's life (DECISIONS.md D-008). The master copy of this list
   lives outside this repository, in the planning folder's git-ignored `_private/`
   directory, and is never committed here.

4. **Connect Codex.** At https://chatgpt.com/codex, under the ChatGPT Pro subscription,
   connect the GitHub integration to `e-skora/petasos` and turn on automatic review for
   every pull request (AGENTS.md section 7).

5. **Later, for change 005 (deployment).** Not needed until that change builds:
   - Create a Fly.io deploy token (a terminal with `flyctl` installed, or the Fly.io
     dashboard) and add it on github.com as `FLY_API_TOKEN`. Checked against the Fly.io
     documentation on tokens, https://fly.io/docs/flyctl/tokens-create/ (2026-09-27).
   - Create a Cloudflare API token with the zone and DNS permissions change 005 needs,
     from the Cloudflare dashboard, and add it as `CLOUDFLARE_API_TOKEN`; also record the
     account id as `CLOUDFLARE_ACCOUNT_ID`. Checked against
     https://developers.cloudflare.com/fundamentals/api/get-started/create-token/
     (2026-09-27).
   - Create the Cloudflare zone for `petasos.io`, point the `api` CNAME at the Fly.io
     app, and create the Cloudflare Pages project that serves `site/` and `demo/dist`.
     Checked against https://developers.cloudflare.com/pages/get-started/ (2026-09-27).

## Part 3: connecting an MCP client to a local server

Once the app is running locally (see the README for the `uv run uvicorn` command), an
MCP client can reach it at `http://127.0.0.1:8080/mcp` with a bearer token from the
`tokens` mapping passed to `create_app`. Not yet tested with a live client.

**Claude Code:**

```
claude mcp add --transport http petasos http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer <token>"
```

Checked against the Claude Code MCP guide, https://docs.claude.com/en/docs/claude-code/mcp
(2026-09-27). Not yet tested with a live client.

**Codex:** add an entry to `~/.codex/config.toml` naming the streamable HTTP transport,
the URL above, and an `Authorization: Bearer <token>` header. The exact key names change
between Codex releases; check Codex's current MCP configuration documentation for the
field names in effect when you do this. Not yet tested with a live client.
