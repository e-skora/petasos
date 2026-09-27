# Petasos

Petasos is an open source trust gate for AI agents: a small Python library plus a live reference server that lets a language model propose actions but never approve them.

Status: being built in public; see changes/ for the build ledger.

## Watch (coming)

A recorded terminal transcript and a short screen recording of the demo app, showing three client identities behaving differently, a canary tripping when someone tries to read the one ticket it is planted in, and an approval held and released.

## Connect (coming)

A live MCP (Model Context Protocol, the standard way an AI assistant calls outside tools) endpoint. Mint a one-hour visitor session with one documented command, then paste the URL and a session token into your own Claude, Codex, or curl session to experience the gate yourself.

## Inspect (coming)

The full repository: tests, the changes/ ledger showing how it was built by agents, and the decisions log.

## Credit

The safety design here is drawn from Talaria, a private personal agent system Elias Skora runs. No code crosses between the two projects in either direction.

## License

MIT. See LICENSE.
