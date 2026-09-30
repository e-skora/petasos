# Plan: 007-demo-app

grounded_at: (set by the planning thread at ratification, after the 004 build merges; the
builder only checks it)

lane: claude

## Wall

The merge gate reads one path per bullet, on one line.

wall_expected:
- `demo/**`
- `changes/007-demo-app/report.md`
- `changes/007-demo-app/tasks.md`

`tasks.md`: ticking boxes only. Nothing under `src/`, `tests/`, `docs/`, `scripts/`, or
`site/` is in this wall: the app consumes 004's endpoints as merged and changes no server code.
If a 004 response does not carry what a screen needs, stop and flag; do not add an endpoint
or a field.

wall_forbidden: everything standing-forbidden in AGENTS.md section 1, plus:
- `src/**`
- `tests/**`
- `docs/**`
- `scripts/**`
- `site/**`
- `Dockerfile`
- `.dockerignore`
- `fly.toml`

The merge gate enforces this list mechanically from the copy of this file on `main`.

## Parallel-safety note

This change depends on 004 and may build in parallel with 005 (deploy); the walls are
disjoint (005 never touches `demo/**`). Under the strict up-to-date rule, whichever merges
second gets one "behind main" repair from the builder; that repair is a merge of `main`, not
new work.

The CI `demo` job starts running for real the moment `demo/package.json` exists on the
branch: `npm ci`, then `npm test`, then `npm run build`, on Node 22 (the newest 22.x
`setup-node` resolves; jsdom 30 needs 22.22.2 or newer, and `engines` says so). It is a
required check for merge. `deploy-site.yml` (hand-started, 005's runbook) copies `demo/dist`
into `site/demo` and deploys `site/` to Cloudflare Pages; `site/` itself is 006's.

## Build order

1. Project files (spec 7.1): `package.json` with the exact pins and scripts, `npm install`
   to produce `package-lock.json`, `tsconfig.json`, `vite.config.ts` (from `vitest/config`,
   `base: "/demo/"`, jsdom), `index.html`, the three `.env` files, `styles/tokens.css`.
   Spec test 9.
2. `router.ts`, `api.ts` (envelope and the 409 mapping, the role token, the failure
   sentence), `session.ts` (single-flight mint, `localStorage`, expiry, 401 and 429 rules),
   `copy.ts` (the plain-words table and every fixed sentence), and the fixtures (spec 7.2,
   7.3, 7.6). Spec tests 1, 2, 10.
3. `hold.ts` and `HoldButton`, `ApprovalCard`, `Inbox`, `Pill`, `Switcher`, `TopBar`
   (spec 7.4, 7.5). Spec tests 3, 4, 5.
4. `TicketList`, `TicketPage`, `LedgerTable`, `CanaryPage`, `Scenario`, `app.tsx`,
   `main.tsx` (spec 7.3, 7.4). Spec tests 6, 7, 8, then the reader test 11.
5. `npm ci && npm test && npm run build`; freeze a commit; review the diff against every
   acceptance test; run the wall check; write `report.md`; open the pull request.

## Facts the planning thread verified (2026-09-30, npm registry and a scaffold on Node 22.23.2)

- The pinned versions in spec 7.1 install together with no peer warnings; `vitest run`,
  `tsc --noEmit`, and `vite build` pass on a scaffold with them.
- `base: "/demo/"` yields `/demo/assets/...` URLs in `dist/index.html`.
- `defineConfig` from `vite` rejects a `test` block under `tsc --noEmit`; `vitest/config`
  accepts it.
- Vitest runs in mode `test` and loads neither `.env.development` nor `.env.production`.
- jsdom 30.1.1 has `PointerEvent` but no `setPointerCapture`, `releasePointerCapture`,
  `hasPointerCapture`, or `window.matchMedia`; a keyboard Enter never becomes a `click` there.
- CI runs `npm test` before `npm run build`, so `dist/` does not exist at test time.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if any pinned package fails to install under Node 22 in
CI, flag the package and version rather than loosening the pin. If a 004 response lacks a
field a screen needs, flag it (the fix is a 004 follow-up, not an app-side guess). If any
test needs a real timer, a real browser, or the network, flag it.
