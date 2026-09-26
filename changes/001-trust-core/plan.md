# Plan: 001-trust-core

grounded_at: (set by the planning thread when this change is ratified, to the `main` commit
that contains the merged 000-bootstrap; the builder checks it and never fills it in. This
change cannot be claimed until 000-bootstrap has merged.)

lane: claude

## Wall

wall_expected:
- `src/petasos/trust/**`
- `src/petasos/ledger/**`
- `tests/test_trust_*.py`
- `tests/test_ledger_*.py`
- `changes/001-trust-core/report.md`
- `changes/001-trust-core/tasks.md` (ticking boxes only)

wall_forbidden: everything standing-forbidden in AGENTS.md section 1, which already covers
`changes/001-trust-core/proposal.md` and `changes/001-trust-core/spec.md` under the
`changes/**/proposal.md` and `changes/**/spec.md` patterns. Do not touch `src/petasos/mcp/**`,
`src/petasos/memory/**`, `src/petasos/helpdesk/**`, or `src/petasos/owner/**`: those belong to
later changes and do not exist yet.

## Parallel-safety note

Change 002 (memory-canary-guard) can build at the same time as this one: its wall is
`src/petasos/memory/**` and disjoint test files, so the two branches do not touch the same
files and can merge in either order.

## Build order

Follow `src/petasos/trust/` and `src/petasos/ledger/` as laid out in ARCHITECTURE.md section
3, building the parts each later part depends on first:

1. `trust/manifest.py`: the `Manifest` dataclass (spec 1.1), with a test proving it has no
   attribute containing "tier" (acceptance test 1).
2. `ledger/store.py` and `ledger/chain.py`: the hash-chained ledger, since grants and the
   executor both write to it (spec 1.13, 1.14; acceptance tests 12, 13, 15).
3. `trust/tiers.py`: `derive_tier` (spec 1.2), with the hypothesis-based monotonicity test
   (acceptance test 2). **This is the acceptance test that uses hypothesis, per
   ARCHITECTURE.md section 8 and DECISIONS.md D-003.**
4. `trust/verbs.py`: `verb_for` and the verb table (spec 1.4), with the enumeration test
   proving every producible verb has a registered executor (acceptance test 3).
5. `trust/grants.py`: `GrantStore` (spec 1.5 to 1.10), covering staging, the two-view split
   (acceptance test 4), concurrent approval (acceptance test 5), verb-mismatch burn
   (acceptance test 6), non-approver refusal (acceptance test 7), expiry sweep (acceptance
   test 8), and the rate rails (acceptance test 9). The clock is injected per spec 1.15.
6. `trust/executor.py`: `Executor.run` (spec 1.11), covering hash-mismatch refusal
   (acceptance test 10).
7. `trust/constraints.py`: hard constraints (spec 1.12), covering the `disable_gate` denial at
   every tier (acceptance test 11).
8. The outcome-sentence table (spec 1.16): a fixed dict from result code to plain sentence.
   ARCHITECTURE.md's module list does not name a separate file for this; put it wherever it is
   most naturally imported by both `grants.py` and `executor.py` (a new small module under
   `trust/`, or folded into one of the existing files) and say which you chose in
   `report.md`. Covered by acceptance test 14.
9. The import-hygiene test (acceptance test 15): the whole module imports with no network, no
   environment variable, and no file outside a temporary directory.

Each task in `tasks.md` names which acceptance test numbers from `spec.md` section 3 it
satisfies.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if the outcome-sentence table's home is genuinely
ambiguous between two equally reasonable module layouts, pick one, record the choice and the
reasoning in `report.md`, and do not treat the choice itself as a stop-and-flag; only flag if
neither `grants.py` nor `executor.py` can import it without a circular import.
