# Spec: 002-memory-canary-guard

Binding once the proposal is ratified. The builder implements this table; a task that seems to need a different answer is a stop-and-flag.

Version 2, 2026-09-29, answering `review/2026-09-27-specs-002-003.md` findings 1 (one expiry rule for `put` and `put_in`, 2.3, 2.4a, 2.9), 2 (real scan bounds and a registry-independent matching rule, 2.12), and 9 (stable embedding, 2.4, 2.5).

Version 1, 2026-09-27. Written against `main` at e61705a (000 and 001 merged), so every interface named here exists in code, not only in ARCHITECTURE.md.

## 0. Terms used here

- **Memory tier (T0 to T5)**: how private a stored fact is. T0 public, T1 low, T2 internal, T3 sensitive, T4 private, T5 private-local. A different ladder from the risk tiers L1 to L5 in `petasos.trust`; they share no code and no type.
- **Entry**: one stored fact: a key, a text, a tier, an optional expiry, and (at T4 or above) one canary.
- **Ceiling**: the highest tier a reader may see. Every read names its ceiling; there is no default.
- **Canary**: a random token planted inside the text of a T4-or-above entry when the entry is first stored. It is registered in a table and never minted again for that entry. If it ever appears in an outgoing payload, private text left the process.
- **Guard**: `assert_no_private_payload(payload, canaries)`: scans a payload for any registered canary and for privacy-labelled keys, and raises on a hit. It takes no destination, identity, or ceiling: it cannot be told "this reader is allowed".
- **Privacy-labelled key**: a dictionary key, at any depth, equal to `private` or starting with `private_`. Server code that must never serialise a field names it this way, and the guard catches it if it does.
- **Hit**: what the guard found: `kind` (`canary`, `private_key`, or `guard_error`) and, for a canary, the token; the entry it belongs to is looked up separately (2.14).

## 1. Decisions for this change

| # | Decision | Choice | Why |
|---|---|---|---|
| 2.1 | Tier type | `MemoryTier(Enum)` in `memory/store.py` with members `T0` to `T5` (values `"T0"` to `"T5"`) and a total order (`T0 < T1 < ... < T5`). `petasos.memory` never imports `petasos.trust`; a test asserts it (acceptance test 1). | ARCHITECTURE.md vocabulary: a different ladder, sharing no code. |
| 2.2 | Tables | `memory_entries(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, key TEXT NOT NULL, tier TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT, canary_id INTEGER, UNIQUE(scope, key))` and `canaries(id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, entry_id INTEGER NOT NULL, minted_at TEXT NOT NULL)`. `MEMORY_SCHEMA` in `memory/store.py`; `Database.migrate()` imports it at call time like the ledger and grants schemas (a two-line edit to `storage.py`, inside this change's wall). | One SQLite file, each package owns its tables (ARCHITECTURE.md section 6). |
| 2.3 | Store | `MemoryStore(db: Database, *, scope: str, clock: Callable[[], datetime])`. `put`, `read`, `search`, and `sweep` each open their own connection or transaction, filter on `scope`, and sweep this scope's expired entries first, inside that same transaction (so a `put` whose write fails rolls back its sweep too). `put_in` (2.4a) is the one exception: no transaction of its own and no sweep. `scope` is a trusted string from the calling layer (003 passes the visitor session id), never from request content. | Same shape as `GrantStore` (001 spec 1.26). |
| 2.4 | Put | `put(key: str, text: str, tier: MemoryTier, *, expires_at: datetime \| None = None) -> Entry`. Inserts or updates the row with that `(scope, key)`. `key` and `text` are refused when empty, when `key` exceeds 128 characters, or when the caller's `text` exceeds 8,192 characters (`InvalidEntry`); the cap applies to the caller's text before the marker is added, in this order: strip this entry's own marker from the caller's text (2.5), check the remainder against the cap, then append the marker; so the stored text of a T4-or-above entry may be up to 8,192 plus 32 characters (the marker is ` [ref ` + 25 + `]`), and reading an entry and putting its `text` back never fails on length. `expires_at` must be timezone-aware and later than `clock()`, else `InvalidEntry`. | Bounded inputs; the launch limits in 005 sit above these. |
| 2.4a | Put inside a caller's transaction | `put_in(conn: sqlite3.Connection, key, text, tier, *, expires_at=None) -> Entry` does exactly what `put` does but on the connection it is given, issuing no `BEGIN` or `COMMIT`, so a caller that must write its own rows and a memory entry atomically (003's session mint) can do so in one transaction. `put` is `with self.db.transaction() as conn: self._sweep_in(conn); return self.put_in(conn, ...)`, where `_sweep_in(conn)` is the sweep on a given connection and the public `sweep()` is `with self.db.transaction() as conn: self._sweep_in(conn)`. `put_in` is the only public method that takes a connection; it does not sweep. Both `put` and `put_in` treat an expired row for the key as gone: `put_in` deletes it inside the caller's transaction and inserts a new entry with a new id (`put` reaches the same state through its sweep); the old row's canary stays registered (2.7) and the new entry, if at T4 or above, mints its own canary (2.5 applies per entry identity, and the expired entry no longer exists). `TierLocked` is judged only against a live row. A failed `put_in` rolls back with the caller's transaction, replacement delete included. | Same convention as `ledger.append(conn, ...)` and a tool's `effect(conn, ...)`: one transaction, opened by the outermost caller (D-016). |
| 2.5 | One canary for life | On the first `put` of a key at T4 or above, one canary is minted, registered in `canaries` with the entry id, and planted in the stored text as ` [ref ` + token + `]` appended after the text. On any later `put` or `put_in` of that key while the entry is live, the same canary is re-planted and no new canary is minted: the store first removes every occurrence of this entry's own marker (` [ref <token>]`) from the caller's text, then appends the marker once, so putting back a text read from the store still stores exactly one marker. Other registered tokens inside the text are left as they are (they are not this entry's canary; if that text ever leaves the process the guard catches them). A key first stored below T4 and later raised to T4 or above mints its one canary at that moment. Lowering a key that has a canary below T4 raises `TierLocked` and changes nothing. | ARCHITECTURE.md invariant 12. Re-minting would let a leak of the old text go undetected; lowering would let private text out without its marker. |
| 2.6 | Canary token | `mint_canary() -> str` in `memory/canary.py`: `"cn-" + secrets.token_urlsafe(16)` (25 characters, URL-safe, no whitespace). Tokens are unique by the table's UNIQUE index; a collision on insert is retried once, then raises. `all_canaries(conn) -> CanarySet` returns every token in the table across every scope, validated once (2.12). | A fixed prefix keeps the demo readable; the guard matches whole tokens, not the prefix. Hygiene rule 1: this prefix is invented here. |
| 2.7 | Canaries outlive entries | Rows in `canaries` are never deleted or updated by this package: not on expiry, not on sweep, not on any `put`. An expired entry's canary still trips the guard. | A leak of old private text is still a leak. |
| 2.8 | Read | `read(key: str, *, ceiling: MemoryTier) -> Entry \| None` returns the entry only if it exists, is not expired, and its tier is at or below `ceiling`. `search(query: str, *, ceiling: MemoryTier, limit: int = 20) -> list[Entry]` returns unexpired entries at or below `ceiling` whose `key` or `text` contains `query` (case-insensitive substring; empty query matches all), newest `updated_at` first, at most `limit` (1 to 100, else `InvalidEntry`). `ceiling` is keyword-only with no default. `Entry.text` is the stored text, canary included. | ARCHITECTURE.md invariant 18 is enforced here per call; which ceiling each identity gets is 003's decision. |
| 2.9 | Expiry | An entry whose `expires_at` is at or before `clock()` is expired: the expiry instant counts. `sweep()` deletes expired entries of this scope (not their canaries) and runs at the start of every public method. `read` and `search` never return an expired entry even before a sweep. | Same boundary rule as grants (001 spec 1.14). |
| 2.10 | Entry view | `Entry(id: int, scope: str, key: str, tier: MemoryTier, text: str, created_at: datetime, updated_at: datetime, expires_at: datetime \| None, canary: str \| None)`, frozen. `canary` is the token for T4-or-above entries, else `None`. | The caller can see which marker belongs to which entry; the guard reports the entry id on a hit. |
| 2.11 | Guard | `scan(payload, canaries: Collection[str]) -> Hit \| None` and `assert_no_private_payload(payload, canaries: Collection[str]) -> None` (raises `GuardTripped(hit)`) in `memory/guard.py`. Neither function has a parameter for destination, identity, route, or ceiling (acceptance test 17). | ARCHITECTURE.md invariant 13: destination-blind. |
| 2.12 | What the guard scans, and its bounds | `payload` may be `str`, `bytes`, `int`, `float`, `bool`, `None`, or a `dict`, `list`, or `tuple` of those, nested. Every `str` and `bytes` (decoded as UTF-8 with errors replaced) is scanned, in dictionary keys and values alike. Every `dict` key that is a privacy-labelled key (`private` or `private_` prefix, case-sensitive) is a hit even when its value is empty. Bounds, all counted cumulatively over the whole payload and all giving a `guard_error` hit the moment they are crossed (never skipping the rest, never truncating the token set): `MAX_NODES = 10_000` nodes, where every dict, list, tuple, key, and scalar value is one node; `MAX_TEXT_BYTES = 2_097_152` (2 MiB) of scanned text, counting each `str` by its UTF-8 length and each `bytes` by its length; `MAX_DEPTH = 64`. Any other object type, or any exception raised while scanning, is also a `guard_error` hit. Canary matching does not depend on the size of `canaries`: the scanner finds every occurrence of the prefix `cn-` in the text and tests the 25-character window starting there for membership in the set, so the cost is linear in the text and constant per candidate; `canaries` is either a `CanarySet` (a `frozenset[str]` subclass built by `canary_set(tokens)`, which validates every element against `^cn-[A-Za-z0-9_-]{22}$` once, raising `ValueError` on a malformed one) or any other collection, which `scan` converts with `canary_set` on that call; a malformed element in that case makes the whole scan a `guard_error` (a malformed registry is not silently ignored). A caller that scans often, like 003's seam, builds one `CanarySet` per registry version and reuses it, so validation is not repeated per scan. The first hit found wins; canary hits are found before key hits at the same node. | ARCHITECTURE.md invariant 14: a broken guard aborts the response rather than serving it. Bounds on every accepted input, and a match rule that stays fast as the registry grows for the life of the deployment (2.7), keep a hostile payload or a large registry from turning the guard into a hang. |
| 2.13 | Hit and exception | `Hit(kind: Literal["canary", "private_key", "guard_error"], token: str \| None, key: str \| None, detail: str)`, frozen; `token` is set for canary hits, `key` for private-key hits, `detail` is a plain sentence. `GuardTripped(Exception)` carries `.hit`. The three sentences, fixed in `guard.py`: canary, "Private memory was about to leave the system, so the response was stopped."; private key, "A field marked private was about to leave the system, so the response was stopped."; guard error, "The response could not be checked for private data, so it was stopped." No sentence names the token, the key, or the error. | Never a marker on a screen. |
| 2.14 | Entry ids on canary hits | `scan` returns the token; the caller that also has the database (003's seam) may look up `entry_id` and `scope` with `canary_entry(conn, token) -> tuple[int, str] \| None`. The guard itself opens no connection and reads no file. | The guard stays pure so it can run on every response without touching storage. |
| 2.15 | Clock and purity | `MemoryStore` takes `clock` and derives every timestamp from it. Importing `petasos.memory` touches no network, environment variable, or file. | 001 spec 1.24; ARCHITECTURE.md section 10. |
| 2.16 | Ledger | This change writes no ledger rows. Recording a guard trip (kind `refused`, detail `{"code": "guard/canary"}` or `{"code": "guard/private_key"}`, never the token) is 003's seam, which has the identity and the route. | The ledger's actor column needs an identity; this package has none. |

## 2. Public interface

```python
from datetime import timedelta
from petasos.storage import Database
from petasos.memory import (
    MemoryStore,
    MemoryTier,
    Entry,
    mint_canary,
    all_canaries,
    canary_entry,
    scan,
    assert_no_private_payload,
    GuardTripped,
    Hit,
)

db = Database(tmp_path / "petasos.sqlite")
db.migrate()  # now also creates memory_entries and canaries
store = MemoryStore(db, scope="session-1", clock=clock)

store.put("customer.tone", "Prefers short replies.", MemoryTier.T1)
secret = store.put("ticket-7.private", "Card ending 4242 was charged twice.", MemoryTier.T4)
secret.canary  # "cn-..." ; secret.text ends with " [ref cn-...]"
again = store.put("ticket-7.private", "Charged twice; refund pending.", MemoryTier.T4)
again.canary == secret.canary  # True: never re-minted

store.read("ticket-7.private", ceiling=MemoryTier.T3)  # None
store.read("ticket-7.private", ceiling=MemoryTier.T4).text  # the text with the canary
[e.key for e in store.search("ticket", ceiling=MemoryTier.T1)]  # []  (T4 hidden)

with db.connect() as conn:
    canaries = all_canaries(conn)
scan({"reply": "All good."}, canaries)  # None
scan({"reply": secret.text}, canaries)  # Hit(kind="canary", token=secret.canary, ...)
assert_no_private_payload({"private_note": ""}, canaries)  # raises GuardTripped (private_key)
```

`petasos.memory.__init__` exports exactly the names above plus `MEMORY_SCHEMA`, `InvalidEntry`, `TierLocked`, `CanarySet`, `canary_set`, and the guard bounds `MAX_NODES`, `MAX_TEXT_BYTES`, `MAX_DEPTH` (spec 2.12). `MemoryStore.put_in` (spec 2.4a) is a method, not a separate export.

## 3. Acceptance tests (plain language; each becomes a test named for it)

Tiers and purity

1. `petasos.memory` imports nothing from `petasos.trust` (inspect `sys.modules` after a fresh import in a subprocess), and `MemoryTier` is not `petasos.trust.Tier`. `MemoryTier.T0 < MemoryTier.T5` and the six members are totally ordered.
2. Importing `petasos.memory` in a subprocess with an empty environment and a read-only current directory touches no file, no environment variable, and no network. Constructing `MemoryStore` creates no file; `Database.migrate()` creates `memory_entries` and `canaries`, and running it twice is a no-op.

Storing and canaries

3. `put` at each of T0, T1, T2, T3 stores the text unchanged, returns `canary=None`, and leaves `canaries` empty.
4. `put` at T4 and at T5 each return an entry whose `canary` starts with `cn-`, is 25 characters, and appears in `text` exactly once at the end in the form ` [ref <token>]`; `canaries` has exactly one row per such entry, with the entry's id.
5. Putting the same key at T4 three times with different texts keeps one canary token, one `canaries` row, and re-plants that token in the newest text (never re-minted).
6. A key stored at T2 and then put again at T4 gains exactly one canary at that moment; a further put at T5 keeps it.
7. Putting a key that has a canary at T3 raises `TierLocked`; the entry is unchanged and no row was written.
8. `put_in` on a connection inside `db.transaction()` writes the entry and its canary in that transaction: raising after the call leaves no entry and no canary row; committing leaves both. Empty key, empty text, a 129-character key, an 8,193-character text, an `expires_at` without a timezone, an `expires_at` equal to `clock()`, and `limit=0` or `limit=101` on `search` each raise `InvalidEntry`; the table is unchanged afterwards.
9. A T4 entry with `expires_at` exactly at the frozen clock is overwritten through `put` in one fixture and through `put_in` in another, with no read in between: in both, the result has a new entry id, the requested tier (T0 in one case, T4 in the other, no `TierLocked`), a new canary when T4, and the old token is still in `all_canaries` and still trips `scan`. In the `put_in` fixture, raising after `put_in` rolls back the caller's other writes, the replacement delete, and the new entry together.
10. Reading a T4 entry and putting its `text` back (through `put` and through `put_in`) stores exactly one marker, the same token; a caller text of exactly 8,192 characters at T4 is accepted, its stored text is 8,224 characters, and putting that stored text back is accepted too and stores the same 8,224 characters.
11. 10,000 calls to `mint_canary` produce 10,000 distinct tokens (hypothesis or a loop), each matching `^cn-[A-Za-z0-9_-]{22}$`.
12. `all_canaries` returns tokens from every scope: a canary minted in scope `s2` is in the set read while working in scope `s1`.

Reading, ceilings, expiry

13. With entries at every tier T0 to T5 under one key each, `search("", ceiling=X)` for each ceiling X returns exactly the entries at or below X, and `read` of a T5 key with ceiling T4 is `None`.
14. `search` is case-insensitive on key and text, returns newest `updated_at` first, honours `limit`, and never returns an entry from another scope.
15. An entry with `expires_at` one second after `clock()`: readable now; with the clock advanced to exactly `expires_at` it is `None` from `read`, absent from `search`, and the next public call (`put` of another key, `read`, `search`, or `sweep`, each tested) deletes its row; its `canaries` row is still present and its token is still in `all_canaries`.
16. Two stores on one database with scopes `s1` and `s2`: a key put in `s1` is `None` in `s2`; `sweep` in `s2` deletes nothing of `s1`.

The guard

17. `scan` and `assert_no_private_payload` take exactly two parameters, `payload` and `canaries`; no parameter name contains destination, identity, client, route, or ceiling (reflection, like 001 acceptance test 5).
18. A payload containing a registered canary in a nested dict value, in a list item, in a dict key, inside a longer sentence, and as `bytes`, is a `canary` hit each time, naming that token; the same payload with the token's last character changed is clean.
19. A dict key `private`, `private_notes`, or `private_x` at any depth is a `private_key` hit even with an empty value; `Private_notes`, `notes_private`, and `privately` are not.
20. `assert_no_private_payload` raises `GuardTripped` whose `.hit` equals what `scan` returned, and whose `str()` is the plain sentence with no token or key in it.
21. A payload holding an object of an unsupported type (a set, a datetime, a class instance), a payload nested 65 levels deep, and an object whose iteration raises, each give a `guard_error` hit; `scan` never raises.
22. Each bound is tested at the limit and one over it, counting the root container as a node: a tuple of 9,999 scalars (10,000 nodes) scans clean and one of 10,000 scalars is `guard_error`; a list of 9,999 empty dicts clean and of 10,000 `guard_error`; a list of 3,333 dicts `{"k": "v"}` (1 + 3,333 x 3 = 10,000 nodes) clean and of 3,334 `guard_error`; a single string of 2,097,152 bytes clean and one byte more `guard_error`; the same for `bytes`, and for text spread over many strings that together cross 2 MiB; nesting 64 deep clean and 65 `guard_error`.
23. A registry of 100,000 tokens (including one minted in another scope for an entry that has since expired and been swept) with a 1 MiB clean payload scans in under one second on the CI runner, and the expired scope's token still trips when planted; a plain list containing one malformed element (`cn-short`) makes every scan a `guard_error`, and `canary_set` of that list raises `ValueError`; a `CanarySet` passed to `scan` is used as is (patch `canary_set` and assert it is not called).
24. A 1 MB payload of clean text with 1,000 registered canaries scans in under one second on the CI runner (a bound, not a benchmark).

Reader test

25. Plant a T4 entry, read it back with ceiling T4, put its text in a payload, call `assert_no_private_payload` with `all_canaries(conn)`: it raises `GuardTripped` with kind `canary` and `canary_entry(conn, hit.token)` returns the entry's id and scope. The same with a ceiling of T3 returns no entry, so there is nothing to leak.
