# PRODUCT.md: what Petasos is

Status: PROPOSED (drafted 2026-09-26, awaiting Elias's ratification). Once ratified, this file is the source of truth for scope. If a chat, a task, or an agent disagrees with it, this file wins until it is deliberately revised and its version bumped.

Version: 0.1-proposed

## 1. One sentence

Petasos is an open-source **trust gate for AI agents**: a small Python library plus a live reference server that lets a language model *propose* actions but never *approve* them.

The server speaks **MCP** (Model Context Protocol), the open standard an AI assistant such as Claude or Codex uses to call outside tools. Any assistant that speaks MCP can connect to Petasos and try to act; Petasos decides what happens next.

Plain version: an AI assistant can read things on its own, but the moment it wants to send an email, spend money, or delete something, Petasos stops it, works out how risky the action is using fixed rules (not the AI's opinion), and makes a human press and hold to let it through. Everything is logged in a way that shows if anyone tampered with the log.

## 2. Why it exists

Elias is applying for forward-deployed engineer (FDE) and AI-integration roles. His strongest proof of production-grade agent-safety work is Talaria, a private personal agent he runs, which nobody can inspect. Petasos re-implements Talaria's safety design in public, from scratch, with nothing copied, so a reviewer can read it, watch it, and connect to it.

The design is openly credited: "drawn from Talaria, a private production system I run." Talaria are Hermes' winged sandals; the petasos is Hermes' winged hat. Same maker, smaller artifact, and a hat is what you put on before you go out into the world.

## 3. The takeaway sentence

Every part of the project is ranked against this: a reviewer who spends two minutes with the README walks away thinking

> "This person applies systems thinking and detail-level rigor to agent safety, and that rigor visibly produces better outcomes."

## 4. The reviewer ladder

Each rung deepens belief in the takeaway sentence. Each is a separate deliverable.

1. **Read** (2 minutes): the README case study. Leads with WHY each safety choice exists, then shows it.
2. **Watch** (5 minutes): a recorded terminal transcript and a short screen recording of the demo app. Three client identities behave differently; a canary catches a leak; an approval is held and released.
3. **Connect** (10 minutes): the reviewer pastes `https://api.petasos.io/mcp` and a published demo token into their own Claude, Codex, or curl, and experiences the gate personally.
4. **Inspect** (30 minutes): the repo. Tests, the `changes/` ledger showing how it was built by agents, the decisions log.

## 5. Who it is for, and not for

**For:** hiring managers and engineers screening FDE / AI-integration candidates; interviewers who want concrete talking points; developers who want a worked example of agent-action gating over MCP.

**Not for:** anyone wanting a real help-desk product; anyone wanting a framework to `pip install` into production without reading it (it is a reference implementation and it says so); and it is NOT a Talaria feature, client, or release. No code crosses between the two repositories in either direction, ever.

## 6. What gets built (locked scope for v1)

One repository, three deliverables.

### 6.1 The library: `petasos` (Python package)

| Module | What it does, in plain language | Source of the design |
|---|---|---|
| `petasos.trust` | Turns a structured description of an action (the **manifest**) into a risk level L1 to L5 using fixed rules. Issues **grants** for risky actions: a single-use token bound to a verb, with an expiry, a rate limit per hour, and an abort switch. Runs the action only after a human confirms, and re-checks the manifest right before running. | Talaria trust gradient |
| `petasos.ledger` | An append-only audit log where each row's hash includes the previous row's hash, so editing history is detectable. States plainly which tables are chained and which are not. | Talaria trust ledger |
| `petasos.memory` | A tiny tiered memory store (tiers T0 public to T5 private-local). Anything at T4 or above gets a hidden **canary** token. A **guard** scans every outgoing payload for canaries and aborts on a hit, no matter who is asking. | Talaria canaries and privacy guard |
| `petasos.mcp` | An MCP server over plain HTTPS (the "streamable HTTP" transport, which is the standard way to reach an MCP server on the internet). Unauthenticated requests get a 401 *before* the protocol handshake, so a stranger never learns which tools exist. Each client identity has a memory-tier ceiling and an action scope. Browsers are refused. Every response passes through the guard; the only exemptions are listed in one place and a test fails if a new one appears. | Talaria MCP spine |
| `petasos.explain` (stretch) | Every gate verdict carries one plain-English sentence built only from facts the gate has ("Staged for approval because it sends email to an external address"). | Talaria routing-reason tap, adapted |
| `petasos.intent` (stretch) | A pure text module answering a different question from the tier rules: "did the human actually ask for this?" Reported speech, negation, and completion are vetoes. Uncertainty resolves toward asking. | Talaria intent gate |

### 6.2 The reference server and demo app: "Hermes Helpdesk"

A fake customer-support desk with obviously invented tickets and customers.

Tools exposed over MCP (the tier is derived by rule from each tool's manifest, never declared):

| Tool | What it does | Expected tier |
|---|---|---|
| `list_tickets`, `get_ticket` | Read tickets | L1 |
| `add_internal_note` | Append a private note to a ticket (reversible, internal) | L2 |
| `reply_to_customer` | Send an email to the customer (external write) | L4: staged for approval |
| `issue_refund` | Move money | L5: staged, longer hold, crimson |
| `delete_ticket` | Irreversible delete | L5 |
| `list_pending_approvals`, `approve`, `abort` | Owner-only approval surface | walled to owner |

Three published demo client identities: `visitor` (reads only, T0 memory ceiling), `agent` (an external model: reads plus staged writes, T3 ceiling), `owner` (approves and aborts, T4 ceiling). One hidden canary ticket exists that must never appear in any listing; fetching it by id from any client trips the guard and is logged.

The demo web app (served from petasos.io) shows: the ticket list, an approvals inbox with press-and-hold (1.2 s for L4, 2.5 s for L5), a quiet "N waiting for you" pill, a ledger viewer with a "verify chain" button, and a "trip the canary" button that shows the abort live.

Demo hygiene: tokens are public, so the server rate-limits per token, resets the fake data every hour, and never stores anything a visitor types beyond that hour.

### 6.3 The site: petasos.io

A static site: the case study, the three-rung "try it" path, the recorded transcript, links to the repo and the demo app. Hosted free on Cloudflare Pages.

## 7. Success criteria (checkable)

- [ ] `curl -X POST https://api.petasos.io/mcp` with no token returns 401 and reveals nothing about tools.
- [ ] The same request with an `Origin` header and a valid token is refused (browsers never ride `/mcp`).
- [ ] `visitor`, `agent`, `owner` tokens produce three different tool lists and three different memory result sets from the same query.
- [ ] `reply_to_customer` from `agent` returns `{"status": "staged"}` with no token; `list_pending_approvals` as `agent` is refused; as `owner` it shows the grant.
- [ ] Approving with the right token and the wrong verb burns the grant; approving from the wrong client is refused but the grant survives.
- [ ] Fetching the canary ticket from any client returns a guard abort and writes a ledger row.
- [ ] Editing one ledger row by hand makes `verify` name that row.
- [ ] Test suite runs green in GitHub Actions on every PR; the merge gate is CI, never an agent's claim.
- [ ] A fresh reader (or a fresh Claude session with no context) hands back the takeaway sentence after reading the README.
- [ ] The repo contains no identifier from the private project (a grep-gate test enforces a denylist that CI reads from a secret).

## 8. Out of scope for v1 (do not add back without a new decision)

Vector memory and embeddings; multi-vendor model routing and cost ledger; the judge quorum; the relay engine and eval harness (both listed as v2 candidates in DECISIONS.md); chat, projects, files, scheduler, skills; any real email or payment integration (the demo fakes both); OAuth (bearer tokens only; noted in the README as the first hardening step); dark mode; mobile push; blog or LinkedIn content (separate effort after ship); resume integration.

## 9. Constraints

- **Timeline:** mostly finished within two weeks of ratification (target 2026-10-10). "Mostly" means rungs 1 to 3 live and the stretch modules optional.
- **Elias's time:** about 15 minutes a day: ratify proposals, answer questions, click merge. One-time setup of roughly an hour (accounts, secrets, DNS).
- **Cost:** hosting under $10/month. No LLM API keys required by the build loop (subscriptions only).
- **Hygiene rule 1, patterns not identifiers:** the design is re-expressed; no real token formats, canary prefixes, hostnames, client names, paths, or people from the private project.
- **Hygiene rule 2, separate infrastructure:** own domain (petasos.io), own hosting account, own GitHub repo, fully apart from the private system.
- **Prose rule:** README, site, and every "why" note are plain language. Jargon gets a definition the first time it appears. No em dashes anywhere in the repo.
