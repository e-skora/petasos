# Proposal: 006-readme-transcript-site

status: proposed
lane: claude (Cowork writes the prose)
depends_on: 004-owner-api-and-demo-app, 005-deploy-fly-cloudflare
decisions: D-001, D-002

## Why

Everything before this change is real but unread. PRODUCT.md's takeaway sentence only lands if the README leads with why each safety choice exists, and rungs 1 and 2 of the reviewer ladder need a finished case study, a recorded transcript, and a site to host them.

## What this change delivers

The real README (replacing the placeholder from bootstrap): a case study leading with why, then the reviewer ladder made concrete with live links, naming the one documented command that mints a visitor session rather than a published token. The README storyboard is drafted in week one, before the browser and deploy work lands; this change writes the finished prose against what actually shipped. A recorded terminal transcript and a short screen recording of the demo app, both linked from the README. The `site/` static case-study page, deployed by `deploy-site.yml` to `petasos.io`.

## What this change does not deliver

No new application code and no new trust, memory, or MCP behavior; this change only writes about and links to what already exists and works.

## Reader test

After this merges, a fresh reader (or a fresh Claude session with no other context) reads the README in two minutes and hands back PRODUCT.md's takeaway sentence in their own words.
