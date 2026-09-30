# Tasks: 007-demo-app

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [ ] Create the project (spec 7.1): `package.json` with the exact pins, scripts, and
      `engines`; `package-lock.json`; `tsconfig.json`; `vite.config.ts` from `vitest/config`
      with `base: "/demo/"` and jsdom; `index.html`; the three `.env` files; `tokens.css`.
      Satisfies acceptance test 9.
- [ ] Write `router.ts`, `api.ts`, `session.ts`, `copy.ts`, and the recorded fixtures (spec
      7.2, 7.3, 7.6). Satisfies acceptance tests 1, 2, 10.
- [ ] Write `hold.ts`, `HoldButton`, `ApprovalCard`, `Inbox`, `Pill`, `Switcher`, `TopBar`
      (spec 7.4, 7.5). Satisfies acceptance tests 3, 4, 5.
- [ ] Write `TicketList`, `TicketPage`, `LedgerTable`, `CanaryPage`, `Scenario`, `app.tsx`,
      `main.tsx` (spec 7.3, 7.4). Satisfies acceptance tests 6, 7, 8, and the reader test 11.
- [ ] Run `cd demo && npm ci && npm test && npm run build`; freeze a commit; review the diff
      against every acceptance test; run the wall check; write `report.md`; open the pull
      request.
