# QUESTIONS.md: open questions for Elias

Append-only for agents. Elias answers by deleting the entry (and adding a decision to DECISIONS.md if the answer should hold from here on). Newest at the bottom.

## 2026-09-26 (Cowork thread, drafting the doc set)

1. **Where the git repo lives.** Proposal: initialize `~/code/petasos` on the Mac from this folder's contents (excluding `_private/` and `archive/`), push to a new public `e-skora/petasos`. Alternative: make this folder the repo. Your rule says `~/code/<name>`, so the proposal follows it.
2. **Demo app placement.** Proposal: the demo app is part of the petasos.io site (`petasos.io/demo`), calling `api.petasos.io`. Alternative: `app.petasos.io` as its own Pages project. One project is simpler.
3. **Codex's role.** Proposal: reviewer on every PR plus builder for the two stretch changes (007, 008), kicked off by you from chatgpt.com/codex with a one-line prompt I will write. Alternative: Codex reviews only. The first keeps both agents visibly in the build story.
4. **Auto-merge timing.** Proposal: you click merge for every PR in week one; auto-merge goes on in week two if no PR needed a human fix. Alternative: auto-merge from day one behind the two-review rule.

## 2026-09-26 (Cowork planning thread): status of the four questions above

These are recorded here so no builder reopens them. Only question 2 is still open.

1. Settled: the repository is public `e-skora/petasos`, cloned at `~/code/Petasos` (D-005).
2. Still open, needed by change 004 or 005: where the demo app is served (`petasos.io/demo` or `app.petasos.io`). No change before 004 depends on it.
3. Settled: Codex reviews every builder pull request (D-006, D-015). Changes 007 and 008, the stretch builds proposed for Codex, are out of v1 (D-016).
4. Settled and superseded: no human merges builds; the merge gate does (D-015).

## 2026-09-30 (Claude, building 003-mcp-server-helpdesk)

Spec 003 section 3.11 names the help-desk's fake-effect tables `fake_mail` and
`fake_refunds`. `tests/test_trust_helpers.py` (001's test fixtures, outside this
change's wall) already defines its own ad hoc tables of those exact names for its
own unit tests, built on top of whatever `Database.migrate()` creates. Once 003's
`migrate()` also creates real `fake_mail`/`fake_refunds` tables with a different
column layout, those tables exist first, so the test helper's own
`CREATE TABLE IF NOT EXISTS` becomes a no-op and several `test_trust_executor.py`
and `test_trust_grants.py` tests fail with a missing-column error the moment any
effect runs. I could not find this collision anticipated anywhere in spec.md,
plan.md, or DECISIONS.md, and editing `tests/test_trust_helpers.py` is outside
this change's wall. I resolved it by naming this change's two tables
`helpdesk_fake_mail` and `helpdesk_fake_refunds` instead (no acceptance test or
other change's wall references the literal name `fake_mail`/`fake_refunds`, only
the prose "fake_mail table"/"fake_refunds table"), and left the collision noted
here rather than guessing whether a future change should still rename 001's test
fixture, rename this change's tables back once 001's fixture is retired, or
leave both as they are. Options: (a) ratify the renamed table names as the
standing spelling; (b) have a later change rename `tests/test_trust_helpers.py`'s
fake tables instead and revert 003 to the literal spec names; (c) leave this
question open and treat it as cosmetic, since no interface outside this change's
own tests names these tables.
