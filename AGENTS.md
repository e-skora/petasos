# AGENTS.md: rules for every agent working in this repository

This file is read by Codex (natively) and by Claude Code (through CLAUDE.md, which points here). Humans should read it too. It is short on purpose; the reasoning behind each rule lives in DECISIONS.md.

## 0. Read order at the start of every task

1. This file.
2. `PRODUCT.md` (scope), `ARCHITECTURE.md` (shape and invariants), `DECISIONS.md` (what is settled).
3. The change folder you were assigned: `changes/NNN-slug/proposal.md`, `spec.md`, `plan.md`, `tasks.md`.
4. `changes/QUESTIONS.md` (open questions; if your task depends on an unanswered one, stop).

Then state, in your first message or commit body, what you confirmed on disk: repo root, branch, `main` commit, and the CI status of `main`. Never start from assumptions.

## 1. The one safety mechanism: the file wall

Every change's `plan.md` lists `wall_expected` (paths you may change) and `wall_forbidden` (paths you must not touch). Before you push, run:

```
git diff --name-only origin/main...HEAD
```

If any path is outside `wall_expected` or matches `wall_forbidden`, stop, revert that path, and say so in your report. Your own change's `tasks.md` (ticking boxes) and `report.md` are always allowed, whether or not the plan lists them. Standing-forbidden for every automated run, regardless of any plan: `AGENTS.md`, `CLAUDE.md`, `DECISIONS.md`, `PRODUCT.md`, `ARCHITECTURE.md`, `DESIGN.md`, `changes/**/proposal.md`, `changes/**/spec.md`, `changes/**/plan.md`, `changes/QUESTIONS.md` (append-only exception below), `pyproject.toml`, `uv.lock`, `.github/**` (workflows and the merge gate), `tests/test_no_private_identifiers.py`, `tests/test_merge_gate.py`, `review/**`. The merge gate checks this list, the plan's `wall_expected` and `wall_forbidden`, both sides of every rename, tick-only edits to `tasks.md`, and append-only edits to `changes/QUESTIONS.md` itself, from the copy on `main`, before it merges anything. It also checks that the proposal is ratified, its dependencies are merged, and `main` has not moved in the wall since `grounded_at`.

## 2. How work moves

```
proposal.md  ->  spec.md  ->  plan.md  ->  tasks.md  ->  build (PR)  ->  independent review  ->  merge  ->  report.md
```

- A change is eligible to build when its `proposal.md` has `status: ratified` and no open PR names it in the title.
- **Claim before work.** Create branch `build/NNN-slug` from `main`, push one empty commit `claim: NNN-slug` immediately, before reading further. If the push is rejected, another run owns it: stop. Never retry with a suffixed branch name.
- Ground yourself: `plan.md` names a `grounded_at` commit, set by the planning thread at ratification (never by you). If `main` has moved past it in a way that touches your wall, or the value is missing, stop and flag.
- Build the tasks in `tasks.md` in order. Tick each `- [ ]` to `- [x]` in the same commit that completes it.
- Write tests first or alongside; every invariant in `ARCHITECTURE.md §5` that your change touches gets a test named for the invariant.
- Run the suite locally in your sandbox and report the count, but **CI is the only number that counts**. Never restate a suite number against a different commit than the one it was measured at.
- Freeze, then review: once tasks are done, make a commit, then review your own diff against the spec's acceptance tests before pushing. Fix, commit again. Do not review a moving target.
- Write `changes/NNN-slug/report.md` (shape in §5), commit it, push, open a PR titled `[build] NNN-slug` whose body is the report followed by a section headed exactly `## Wall check` holding the wall-check command and its full output. Mark it ready for review, not draft.
- Do not merge. Do not approve your own PR. Do not edit a proposal's `status`. The merge gate (`.github/workflows/merge-gate.yml`, DECISIONS.md D-015) merges a PR when CI is green, both reviews of the head commit have no blocker, the report says `Verdict: BUILT`, and the wall holds. A PR that still waits two builder runs later for a blocker, a failing check, or a report or wall problem is repaired by the next builder run, at most twice; after that it becomes a draft with a dated entry in `changes/QUESTIONS.md`.

## 3. Stop-and-flag conditions

Stop, write the situation to `changes/NNN-slug/report.md` under `## Flags`, push, and open the PR as a draft, when:

1. The spec and the code on `main` disagree in a way the spec did not anticipate.
2. Completing a task would require changing a file outside your wall.
3. A task needs a judgment call the spec does not settle (a name, a threshold, copy, a schema choice). Also append the question to `changes/QUESTIONS.md` (dated, one paragraph, ends with the options you see). This is the one file outside your wall you may append to.
4. You would need a credential, a real external service, or a network call in tests.
5. Anything would reopen a RATIFIED decision in `DECISIONS.md`.
6. A test that passed on `main` fails and the failure is not explained by your change.

Never improvise past a flag. A draft PR with an honest flag is a good outcome.

## 4. Hard rules

- **Patterns, not identifiers.** Talaria may be named as the design's origin. Nothing else from the private project: no hostnames, service labels, secret-store names, canary prefixes, header names, env prefixes, paths, people. `tests/test_no_private_identifiers.py` enforces a denylist that CI reads from a secret; if it fails, remove the string from your change. Never edit the test.
- **Tests never touch the network or a real service.** Fake email, fake payments, fake clock. Any test that needs a database uses a temporary file, never a path under the repo's data directory.
- **No secrets in the repo**, including "example" tokens that look real. Demo client tokens are derived at deploy time from a seed.
- **Approvals are a button or a hold, never a typed phrase**, and no response carries a ready-to-submit approval string.
- **Fail closed.** Timeouts, exceptions, missing configuration, unparseable input: the restrictive outcome, never the permissive one.
- **Additive by default.** Do not rename or delete public functions, routes, or tables unless the spec says so.
- **Prose rules for anything a human reads** (README, docstrings, UI copy, reports): plain language; define a term the first time; no em dashes (use a comma, a colon, or a period); no operation names or ids in UI copy.
- **Exact pins.** `pyproject.toml` and `uv.lock` change only through a planning pull request. A task that needs a new or different dependency is a stop-and-flag.
- **Commits**: imperative subject under 72 characters, body says what and why. One logical change per commit where practical.

## 5. Return report shape (`changes/NNN-slug/report.md`)

```
# Report: NNN-slug
Verdict: BUILT | BUILT WITH FLAGS | BLOCKED
Summary: one paragraph, plain language, what a reader can now do that they could not before.
Commit: <sha> on branch build/NNN-slug
Tests: CI run <link>; local count <n passed / n failed> at <sha> (advisory)
Changed files: full list
Deviations from spec: none | list, each with why
Dependencies changed: none (a needed change is a flag, not an edit); `uv pip check` result
Flags: none | list
Review findings and disposition: filled in AFTER review, each finding: fixed in <sha> | refuted because ... | accepted and owed as change NNN
Open questions appended to QUESTIONS.md: none | list
```

Before committing the report run `grep -nE '<!--|TODO|TBD|\[fill' changes/NNN-slug/report.md`. Any hit means the report is not finished.

## 6. Review guidelines

(Codex reads this section for its automatic PR reviews. Claude's review plugin follows the same list.)

Review the diff against `changes/NNN-slug/spec.md`, not against your own preferences. Return findings in this shape, one per finding: `SEVERITY (blocker | should-fix | nit) | file:line | what is wrong | how you know (reproduced with a test or command, or reasoned) | suggested fix`. Say REPRODUCED or REASONED explicitly. Do not tally or vote; every finding stands alone. Check specifically:

1. Every acceptance test in the spec has a corresponding test in the diff, and it would fail if the behavior were removed (look for tests that cannot fail).
2. Every invariant in `ARCHITECTURE.md §5` touched by the diff still holds.
3. Fail-closed: each new error path resolves restrictively.
4. No path in the diff is outside the change's `wall_expected`.
5. No network, no real clock, no real filesystem path in tests.
6. No hostname, path, label, or person from the private project the design is drawn from; no secret-looking literal. (CI enforces a private denylist; you enforce judgment.)
7. Plain-language rule in anything user-facing; no em dashes.
8. The report has no placeholders and its changed-file list matches the diff.

Approve only when there are no blockers. A should-fix does not block approval but must be listed. The merge gate treats a finding line that starts with `blocker`, and any Codex `P0` or `P1` badge, as blocking the head commit it was made on.

## 7. Roles

- **Claude Code** (cloud, via the GitHub Action, scheduled every 2 hours and on `@claude`): the default builder. Also runs the code-review plugin on PRs.
- **Codex** (GitHub integration under ChatGPT Pro): automatic independent review on every PR; builds `lane: codex` changes when kicked off from chatgpt.com/codex; answers `@codex fix ...` on PRs.
- **Claude in Cowork** (Elias's planning threads): writes proposals, specs, plans, tasks; runs close-outs; never pushes code to `main`.
- **The ChatGPT reviewer project** (ChatGPT desktop app with Codex, its own clone of this repo): reviews every proposal and spec before ratification for gaps, viability, and better options; writes its reviews and any drafts under `review/` on `review/<topic>` branches and opens a PR; never edits the doc set on `main`. A proposal is not ratified until a review for it exists under `review/`.
- **Elias**: ratifies, answers questions, starts releases, and reads reports. He merges nothing: the merge gate merges builds and the planning thread merges planning PRs after the reviewer's pass (D-015). Only he rules on a proposal's `status` or a decision; `DECISIONS.md` records only his rulings, in his words.

`review/` is standing-forbidden for the builder (it reads it, never writes it).

## 8. Local commands

```
uv sync --locked
uv run pytest
uv run ruff check . && uv run ruff format --check .
cd demo && npm ci && npm test
```

The API runs with `uv run uvicorn petasos.app:create_app --factory --port 8080`. Demo tokens for local runs come from `PETASOS_DEMO_SEED=local`.
