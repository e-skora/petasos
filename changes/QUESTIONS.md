# QUESTIONS.md: open questions for Elias

Append-only for agents. Elias answers by deleting the entry (and adding a decision to DECISIONS.md if the answer should hold from here on). Newest at the bottom.

## 2026-09-26 (Cowork thread, drafting the doc set)

1. **Where the git repo lives.** Proposal: initialize `~/code/petasos` on the Mac from this folder's contents (excluding `_private/` and `archive/`), push to a new public `e-skora/petasos`. Alternative: make this folder the repo. Your rule says `~/code/<name>`, so the proposal follows it.
2. **Demo app placement.** Proposal: the demo app is part of the petasos.io site (`petasos.io/demo`), calling `api.petasos.io`. Alternative: `app.petasos.io` as its own Pages project. One project is simpler.
3. **Codex's role.** Proposal: reviewer on every PR plus builder for the two stretch changes (007, 008), kicked off by you from chatgpt.com/codex with a one-line prompt I will write. Alternative: Codex reviews only. The first keeps both agents visibly in the build story.
4. **Auto-merge timing.** Proposal: you click merge for every PR in week one; auto-merge goes on in week two if no PR needed a human fix. Alternative: auto-merge from day one behind the two-review rule.
