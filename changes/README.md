# changes/: the build ledger

One folder per meaningful change: `NNN-slug/`. Numbers are never reused. Inside:

| File | Written by | When | What it answers |
|---|---|---|---|
| `proposal.md` | Cowork thread (Claude) | first | Why this change, what it delivers, what it does not, which decisions it depends on. Carries `status:`. |
| `spec.md` | Cowork thread | after proposal | Binding decisions for this change (a numbered table), the public interfaces, and the acceptance tests in plain language. |
| `plan.md` | Cowork thread | after spec | `grounded_at` commit, `lane` (claude or codex), file wall (`wall_expected`, `wall_forbidden`), build order, stop-and-flag conditions specific to this change, parallel-safety notes. |
| `tasks.md` | Cowork thread; ticked by the builder | after plan | The ordered checklist the builder drains. Each task names its test. |
| `report.md` | the builder; review section filled after review | after build | What landed. Shape in AGENTS.md §5. |

`status:` on the proposal: `proposed` (drafted) -> `ratified` (Elias said yes) -> `building` (a claim commit exists; derived, not written) -> `in-review` (PR open; derived) -> `merged`. Only Elias writes `ratified`. `merged` is written by the Cowork close-out thread after the merge, never by the builder.

`QUESTIONS.md` at this level is append-only for agents (dated entries) and Elias answers by deleting the entry and, if needed, adding a line to DECISIONS.md.

Planned changes for v1. Proposals exist for 000 to 006; 000 and 001 also have spec, plan, and tasks. Spec, plan, and tasks for 002 onward are written in a Cowork thread once the previous change is ratified, so the builder always has one ratified change ready and one queued:

| NNN | Slug | Lane | Depends on | Target day |
|---|---|---|---|---|
| 000 | bootstrap (plus an authenticated MCP `ping`) | claude | none | 1 |
| 001 | trust-core | claude | 000 | 1 to 3 |
| 002 | memory-canary-guard | claude | 001 (uses its `storage.Database`) | 3 to 4 |
| 003 | mcp-server-helpdesk (plus visitor sessions) | claude | 001, 002 | 4 to 6 |
| 004 | owner-api-and-demo-app (guided refund journey) | codex or claude | 003 | 6 to 8 |
| 005 | deploy-fly-cloudflare (plus launch controls: payload bounds, retention, reset ordering) | claude | 003 | 8 to 9 |
| 006 | readme-transcript-site | claude (Cowork writes prose) | 004, 005 | 9 to 11 |

Days 12 to 14 are reserved for failure cases, accessibility, evidence, and repair. The README storyboard is drafted in week one, before 006. Out of v1 (D-016): 007 explain and 008 intent gate; the old 009 hardening list is split between 003 (session quotas) and 005 (launch controls).

How a change merges: the builder opens `[build] NNN-slug`; CI, the Claude review, and the Codex review run on it; the merge gate (`.github/workflows/merge-gate.yml`, D-015) squash-merges it when all pass. `merged` in a proposal's status is written by the Cowork close-out thread afterward; the builder derives eligibility from merged pull requests, not from that field.
