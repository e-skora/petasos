<!--
This template mirrors the return report shape in AGENTS.md section 5. If this PR is a
builder's report for a change folder, paste the finished changes/NNN-slug/report.md content
below instead of filling this checklist by hand; the shapes match on purpose.
-->

## Report

- [ ] Verdict stated: BUILT, BUILT WITH FLAGS, or BLOCKED.
- [ ] Summary written in plain language: what a reader can now do that they could not before.
- [ ] Commit and branch named.
- [ ] Tests: CI run linked; local count given as advisory only, tied to the commit it was measured at.
- [ ] Changed files listed in full.
- [ ] Deviations from spec listed, each with why, or marked none.
- [ ] Dependencies changed listed with versions and the `pip check` result, or marked none.
- [ ] Flags listed, or marked none.
- [ ] Open questions appended to `changes/QUESTIONS.md` are listed, or marked none.
- [ ] `grep -nE '<!--|TODO|TBD|\[fill' changes/NNN-slug/report.md` was run and came back empty.

## Wall check output

```
$ git diff --name-only origin/main...HEAD

```
