# Deploy

This document explains how a release of the API works, the one-time checklist for
the first deploy, how to roll back, and what the running server does by itself.
Plain language throughout; a term is defined the first time it appears.

## How a release works

A release is one run of the "Deploy API" GitHub Actions workflow
(`.github/workflows/deploy-api.yml`), started by hand from the Actions tab with one
input: the exact commit id to release (40 lowercase hex characters). Elias starts a
release; nothing here starts one automatically, and no merge to `main` triggers one.

The workflow refuses anything that is not that exact shape, refuses a commit that is
not on `main`, and refuses a commit whose continuous integration (CI, the test
suite that runs on every pull request) is not green on that exact commit. Only
after all of that does it deploy with `flyctl deploy --remote-only --app
petasos-api --image-label sha-<short commit> --ha=false`. `--ha=false` turns off
Fly's default of creating a second machine during a rolling deploy; this app always
runs exactly one machine, because the SQLite database and the in-process "MCP"
(Model Context Protocol, the standard an AI assistant uses to call this server's
tools) session state only make sense with one machine holding them.

After the deploy, the workflow proves the release by running
`scripts/smoke_journey.py` against the live server. That script mints a session,
reads the help desk's refund ticket as the "agent" role, proposes a $42.00 refund,
approves it as the "owner" role, and confirms it executed exactly once, reading the
refund back from the ticket itself. Its exit code decides what the workflow reports:

| Exit code | Meaning | Workflow outcome |
|---|---|---|
| 0 | Every step passed | `confirmed`; the run succeeds |
| 1 | A step failed | the run fails |
| 2 | The script was called wrong | the run fails |
| 3 | The server is at capacity, so the journey could not mint a session | the run succeeds with a warning: health is confirmed, the journey is not |
| 4 | The journey ran, but the receipt route is not deployed yet (change 004 not live) | the run succeeds with a warning: the release is not confirmed |

The run's summary page shows the commit, the image label, and the smoke journey's
outcome, with its step-by-step output underneath.

The container's one command is `python -m petasos.serve`, which reads its settings
from the environment, migrates the database if needed, and starts the one uvicorn
worker this app ever runs.

## First deploy checklist (one time)

Before the first deploy, run the one-time setup in `docs/setup.md` part 2, item 5
(the Fly app, the volume). Then set up the certificate and DNS the way Fly
documents for a Cloudflare-proxied app: `fly certs add api.petasos.io`, then `fly
certs setup api.petasos.io`, which prints a `_fly-ownership` TXT record to add in
Cloudflare, plus the `api` CNAME to point at Fly with the orange-cloud proxy on; set
the Cloudflare SSL mode to Full (strict) and turn on Always Use HTTPS; confirm with
`fly certs check api.petasos.io`. Then:

1. `fly config validate` against `fly.toml`, to catch a typo before it reaches Fly.
2. Start a release (above).
3. After it finishes, `fly machines list` must show exactly one machine. If it ever
   shows more than one, stop and investigate before doing anything else; this app's
   data model assumes exactly one machine.
4. `fly status` to confirm the machine is healthy.
5. Read back `kill_timeout` from the running app's config (`fly config show`) and
   confirm it is still 40 seconds, ten seconds above uvicorn's own 30 second
   graceful shutdown window, so the two never race.

## Rollback

A rollback is a release of the previous known-good commit: run the same workflow
again with that commit id. Find it from the workflow's run history (each run names
its commit) or from `fly releases`, which lists each deploy's image.

If the previous image is still in Fly's registry, a faster, exact-image rollback
skips the build step: `flyctl deploy --image registry.fly.io/petasos-api:sha-<old
short commit>`. This still needs `--app petasos-api` and `--ha=false`.

A rollback can only reach a commit from this change (005) onward: an older commit
has no `petasos.serve` entrypoint and no smoke journey script, so the workflow has
nothing to run.

## What a release looks like to a connected client

This app runs as exactly one machine with one volume. A release stops the old
machine and starts the new one; there is a short gap where the health check and
every endpoint are unreachable. Every connected MCP client must reconnect
afterward; the streamable HTTP transport does not survive a machine restart.

## Maintenance commands

The maintenance tick (next section) runs by itself, but an operator can also run
one by hand, over SSH into the running machine:

```
fly ssh console -a petasos-api -C "env PETASOS_DB=/data/petasos.sqlite python -m petasos.sessions.maintenance reset"
```

Setting `PETASOS_DB` on the command line is deliberate: whether the console
inherits the app's `[env]` block is not documented, so this is spelled out every
time. The three commands:

- `reset`: expires every visitor session and identity at once, and lets the normal
  expiry path delete their data in the normal order. Prints
  `reset: sessions_marked=<n> sessions_expired=<m>`.
- `expire`: runs one expiry pass by hand (normally the tick does this every
  minute). Prints `expire: sessions_expired=<n>`.
- `rotate`: runs one ledger rotation by hand (normally the tick does this once the
  ledger passes 10,000 rows). Prints `rotate: archive=<file name or none>
  rows=<n>`.

Each prints one line and exits 0 on success, or exits 1 and prints the error's
class name on failure (never its text, so nothing sensitive leaks into a log).

## What the maintenance tick does

Once a second after the server starts, and then every 60 seconds, the running
process does three things in order: expires any visitor session whose hour is up
(deleting that session's fictional tickets, notes, mail, refunds, memory, grants,
and identities everywhere except the audit ledger and the global counters);
rotates the ledger once it passes 10,000 rows (moving every row into an archive
file on the volume and starting the live ledger again from one row that names the
archive); and prunes archive files older than 90 days, or enough of the oldest
ones that the total stays under 200 MB. A failure in any one step is logged and
does not stop the other two, or the next tick.

See it running in `fly logs`: each tick logs one line with the three counts
(sessions expired, whether a rotation happened, archives pruned). A warning line
names a step that failed, with no further detail.

## Bounds

| Bound | Value |
|---|---|
| Request size (outermost, before any other layer reads the body) | 64 KiB |
| Response body scanned by the privacy guard | 1 MiB |
| One streamed event scanned by the privacy guard | 256 KiB |
| Per-session hourly tool calls, mints, and more | see `docs/setup.md` and `ARCHITECTURE.md` |

## What a visitor sees after a rotation

Once a rotation has happened, the live ledger table holds only the rows written
since then, for every session including ones still running. The demo app's ledger
view and "verify chain" button both report the id of the last row that was
archived, and the app tells a visitor in plain language that older rows were moved
to an archive; it never pretends there is nothing there.

## Where the data lives

One SQLite file at `/data/petasos.sqlite` (write-ahead log mode) on the Fly volume,
plus ledger archives under `/data/archive`, kept for 90 days and under 200 MB in
total.

## Disk

Check free space with `fly volumes list`. A full disk fails every write, including
minting a new session, so this is worth checking if the demo starts refusing
requests for no other visible reason.

## Backups

This app runs with exactly one volume by design (a second volume would mean a
second machine, which the data model does not support). Fly takes a daily snapshot
of the volume and keeps 5 days by default; that snapshot is the only copy. Losing
the machine's host loses the demo's data and ledger history back to the last
snapshot. Since every ticket, customer, and refund here is fictional, that is an
acceptable risk for a reference demo.

## The one-machine rule

Never scale this app past one machine: `fly machines list` should always show
exactly one. The volume can only attach to one machine at a time, and the MCP
session bindings live in that one process's memory; a second machine would see
neither.

## Running as root (first hardening step after v1)

The process inside the container runs as root. Fly's documentation does not state
who owns a mounted volume, and community reports say it is root-owned, needing a
privilege-dropping step this image does not yet take. Adding a non-root user is the
first hardening step planned after v1.

## The edge rate limit

Cloudflare sits in front of `api.petasos.io` with one rate-limiting rule (the free
plan allows exactly one): 30 requests per 10 seconds per IP address on the paths
`/mcp`, `/owner/`, and `/session`. The origin is also reachable directly at the
app's `fly.dev` name, so this rule is a convenience, not the real protection; the
app's own per-session quotas and global ceilings are what actually hold under load.
