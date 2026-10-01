# QUESTIONS.md: open questions for Elias

Append-only for agents. Elias answers by deleting the entry (and adding a decision to DECISIONS.md if the answer should hold from here on). Newest at the bottom.

## 2026-09-26 (Cowork thread, drafting the doc set)

1. **Where the git repo lives.** Proposal: initialize `~/code/petasos` on the Mac from this folder's contents (excluding `_private/` and `archive/`), push to a new public `e-skora/petasos`. Alternative: make this folder the repo. Your rule says `~/code/<name>`, so the proposal follows it.
2. **Demo app placement.** Proposal: the demo app is part of the petasos.io site (`petasos.io/demo`), calling `api.petasos.io`. Alternative: `app.petasos.io` as its own Pages project. One project is simpler.
3. **Codex's role.** Proposal: reviewer on every PR plus builder for the two stretch changes (007, 008), kicked off by you from chatgpt.com/codex with a one-line prompt I will write. Alternative: Codex reviews only. The first keeps both agents visibly in the build story.
4. **Auto-merge timing.** Proposal: you click merge for every PR in week one; auto-merge goes on in week two if no PR needed a human fix. Alternative: auto-merge from day one behind the two-review rule.

## 2026-09-26 (Cowork planning thread): status of the four questions above

These are recorded here so no builder reopens them. None is still open. The 003 builder's table-name question (dated 2026-09-30) is settled 2026-10-01 as its option (a): the tables are `helpdesk_fake_mail` and `helpdesk_fake_refunds` (003 spec 3.11, version 2.2), so that flag is resolved and 003's report verdict is `Verdict: BUILT`.

1. Settled: the repository is public `e-skora/petasos`, cloned at `~/code/Petasos` (D-005).
2. Settled 2026-09-30 (Cowork planning thread, under Elias's standing "use best recommendations on the decisions" of 2026-09-26): the demo app is served at `petasos.io/demo/` from the one Cloudflare Pages project (`deploy-site.yml` already copies `demo/dist` into `site/demo`), and the API's allowed browser origins are `https://petasos.io` and `https://www.petasos.io` plus Vite's local dev server (change 004 spec 4.3, change 007 spec 7.1).
3. Settled: Codex reviews every builder pull request (D-006, D-015). Changes 007 and 008, the stretch builds proposed for Codex, are out of v1 (D-016).
4. Settled and superseded: no human merges builds; the merge gate does (D-015).
