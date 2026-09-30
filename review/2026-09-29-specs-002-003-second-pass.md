# Second-pass review: specifications for changes 002 and 003

Reviewed on 2026-09-29. This is the current disposition for PR #16 at the head below. The first-pass review remains in `review/2026-09-27-specs-002-003.md` as history.

## Verdict

**002: PASS with one wording nit.** Its earlier blockers are closed at the specification level. The planning thread can use this review in the established ratification process; the proposal is still proposed and its grounding commit still needs to be set there.

**003: FAIL, one remaining blocker.** The specified streaming guard event is ignored by the pinned MCP client, so the promised client-visible refusal cannot pass acceptance test 15 after an earlier clean event. Use the standard event described below and strengthen that acceptance test. The other first-pass findings are closed at the specification level.

This verdict reviews planning documents. It does not claim the unbuilt features work, ratify a proposal, merge either PR, or authorize a release. 003 still requires merged 002 and grounding against its actual interface before build.

## Exact scope and evidence

- Repository: `e-skora/petasos`, verified in the reviewer's own local clone; clean starting checkout on `review/specs-002-003`, review commit `edf9b4f215206d5fbd138698a09e3831778f2547`.
- Main and PR comparison base: `e61705a2ff98d5ce457c24839413430b94c03286`.
- Reviewed PR: [#16](https://github.com/e-skora/petasos/pull/16), branch `planning/specs-002-003`, head `ab7ee6ce53b86473c88b85740a66e001cccb9d37`. GitHub confirmed it was open at that exact base and head.
- Correction comparison: first-pass head `348a03bfa85be19618828354131636bb33a67bf1` to the reviewed head. Both the full PR and correction diff contain exactly the six 002/003 spec, plan, and task files plus ARCHITECTURE.md. The architecture changes are version 0.3 and the narrow approval-verb exception in invariant 1. Source, dependency pins, proposals, and decisions did not change.
- Inspected: the seven files, first-pass review, repository authority documents, both proposals, open questions, and existing Gate/executor and pinned MCP transport code. The demo-location question still belongs to later changes.
- CI observed live: [main CI](https://github.com/e-skora/petasos/actions/runs/36355617309) succeeded at the base. PR #16's `python`, `demo`, and `private-identifiers` checks succeeded in [CI run 36637742229](https://github.com/e-skora/petasos/actions/runs/36637742229) at the reviewed head. Its Claude review job was skipped. These checks do not implement or prove the new specifications.
- Executed: `git diff --check e61705a ab7ee6ce` passed. A three-case, in-process MCP wire probe ran with installed `mcp==2.2.0`, the existing app for initialization, and a synthetic ASGI response fixture for the subsequent tool call. It used no network or application data. The first attempt stopped at an import typo (`McpError` instead of the pinned SDK's `MCPError`); the corrected probe completed all three cases.
- No full suite was rerun for this documentation-only correction review. The earlier review's local test count remains evidence only for that earlier run. No local private-denylist pass is claimed.

## Remaining blocker

blocker | `changes/003-mcp-server-helpdesk/spec.md:44` (decision 3.10), `:134` (acceptance test 15), `:138` (acceptance test 19) | REPRODUCED | A later-unit refusal uses an SSE event name that the pinned MCP client ignores. | Send the safe JSON-RPC error as a standard `message` event and require a real-client test of the later-unit path.

Decision 3.10 requires `event: petasos-guard` once response headers have been sent. Acceptance test 15 requires the MCP client to surface the guard's plain sentence, whichever refusal path is used. In pinned `mcp/client/streamable_http.py`, `_handle_sse_event` processes only `message`; other names are logged as unknown and ignored. When the stream then ends without a recognized response, the client synthesizes a connection error instead. Acceptance test 19 observes downstream bytes, which alone cannot detect this client failure.

The probe initialized a real `ClientSession` against the existing app, then called its `ping` tool with three synthetic responses. The two streaming cases put a clean comment event before the refusal, representing the path after a clean unit has released the response headers. Results observed at the client:

| Response fixture | Client error code | Client message | Client error data |
|---|---|---|---|
| First-unit HTTP 409 with the specified JSON-RPC body | -32603 | Private memory was about to leave the system, so the response was stopped. | `{"status":"refused/guard_tripped"}` |
| Clean comment, then `event: petasos-guard`, then close | -32000 | SSE stream ended without a response | `null` |
| Clean comment, then `event: message` with the same error body, then close | -32603 | Private memory was about to leave the system, so the response was stopped. | `{"status":"refused/guard_tripped"}` |

The custom-event case also logged `Unknown SSE event: petasos-guard`. The first-unit 409 design works with this pinned client and should be retained. The third case verifies a small compatible correction for the POST response stream; it does not prove unsolicited GET-stream request correlation or compatibility with every MCP client.

**Bounded correction:** In 003 decision 3.10, replace the custom event name with standard `event: message`, keeping the fixed safe JSON-RPC error payload and terminating the stream. Update the guard step in the plan and tasks to require both first-unit and later-unit client tests. In acceptance tests 15 and 19, force a clean unit before the rejected unit and assert the client's error message and `error.data.status`, in addition to recording every ASGI send and proving zero rejected-unit bytes were released. The first-unit case must also remain covered. Keep the exactly-one ledger/counter transaction and the nonrecursive storage-failure path.

If unsolicited GET streams are tested, specify their termination separately: closing a stream without an outstanding request is not proof that a particular tool call received its error. Do not change the SDK pin or weaken the client-visible acceptance requirement to a generic disconnect.

## Nonblocking wording cleanup

nit | `changes/002-memory-canary-guard/spec.md:32` (decision 2.9) | REASONED from inspected text | The sentence still says sweeping runs at the start of every public method, although corrected decisions 2.3 and 2.4a explicitly exempt `put_in` and distinguish `_sweep_in` from the transaction-opening public `sweep`. | Say that `put`, `read`, and `search` call `_sweep_in` in their own transaction, `sweep` exposes it directly, and `put_in` uses only its matching-key expiry rule.

This is a leftover general sentence, not an unresolved expiry design: the explicit exception, wrapper, replacement identity, rollback rules, tests, plan, and tasks now give the builder a definite answer. It does not block 002's review pass.

## First-pass finding disposition

These closures concern the written contracts and tests to be built, not executed feature tests.

| First-pass finding | Disposition at this head |
|---|---|
| 1. Expiry and transaction-owned writes | Closed. 002 decisions 2.3 and 2.4a settle sweeping, expired-key replacement, new entry identity, retained old markers, and rollback; acceptance tests 9 and 15 cover the boundary. Wording nit above remains. |
| 2. Guard bounds | Closed. Cumulative node, text, and depth limits cover the accepted payload types; prefix-window matching and a validated cached set replace per-token payload searches. 003 sets body/event byte limits. Registry retention and refresh still have a storage cost; no constant-memory deployment claim is made. |
| 3. Read-result channel | Closed. A fresh read capture returns a snapshot only after `executed`; effects return only the outcome string. Failure and concurrent-read tests are specified. This fits the existing executor's transaction and result interface. |
| 4. Scope binding | Closed. Each call gets callbacks closed over authenticated scope, plus its own Gate and capture. Distinct same-number session rows and interleaved/concurrent calls are specified. |
| 5. Complete-unit guard | Partly closed. Buffering before release, SSE line joining, limits, current canary registry, safe route labels, and storage-failure termination are specified. The remaining client-visible streaming refusal defect is the blocker above. |
| 6. Approval verb | Closed. ARCHITECTURE.md invariant 1 and 003 permit only the claimed verb comparison; proposal authority and authenticated identity remain server-owned, and wrong-verb burning remains tested. |
| 7. Ledger privacy | Closed. Guard rows use fixed route labels, verified identity or `anonymous`, and an injected clock. Unknown paths and all ledger columns are tested for text retention. |
| 8. Impossible acceptance results | Closed. Isolation tests distinguish same-number scoped rows; quota replenishment is tested at the store layer while expired HTTP credentials remain refused. |
| 9. Stable marker embedding | Closed. Normalization removes the entry's own marker before checking caller-text length and appends one marker; maximum-length round trips are specified. |
| 10. Missing identity bindings | Closed. Missing, evicted, and expired bindings refuse requests before the SDK; lifecycle and eviction tests are specified. |
| 11. Quota transactions | Closed. Admission and counters share their transaction, mint reservations and seed commit together, later failures retain admitted call slots, and concurrent last-slot/daily-limit/global-state tests are specified. |
| 12. Wire validation and error envelopes | Closed for ordinary tool responses. Raw shape checks precede SDK coercion and quota admission; middleware and adapter refusals use `CallToolResult(isError=True)`. The distinct guard transport problem is covered above. |

## Handoff to the planning thread

Correct only the remaining 003 event contract and corresponding plan/task acceptance references, plus the optional 002 expiry sentence. Return the new exact PR #16 head for a review of that correction delta. Keep all closed corrections, the narrow architecture exception, both file walls, and the deferred 001 items unchanged. No implementation or new owner decision is needed for this correction. The latest review lives in this file; the first-pass file is historical evidence.
