---
title: "Lattice agent"
description: "How the Semantica engine hosts subscription Grok over the signals protocol and records that agent's decisions."
icon: "bot"
---

The Decisions workspace reads the Explorer graph. The agent that writes those decisions does not live in the HTTP process.

`semantica-signals` registers `zndx.agent.v1.Agents` on the lattice port, beside Engine and OIP. A peer calls `Agents/Run`. The engine spawns `grok agent stdio` for that run, with filesystem and terminal access denied, and gives the session one MCP server. That server calls the Semantica HTTP API. The HTTP process keeps the graph. The engine does not keep a second one.

## What a peer can ask for

The offer `semantica/grok-build` advertises these behaviours:

- `ontology-search` and `get_node` read the loaded graph.
- `ontology-ingest` posts a document to `POST /api/ontology/load`.
- `forward-chain` posts facts and rules to `POST /api/reason`.
- `decisions` records a node and a causal edge.
- `lattice-complete` calls `thinking` only when `SEMANTICA_LATTICE_PEERS` names an engine whose `Engine/Status` lists that capability as healthy. This process does not start a local model.

Billing is `BILLING_SUBSCRIPTION`. The child process does not inherit `XAI_*`. The CLI uses `~/.grok/auth.json`. An empty `model_capability` means that subscription session. `model_capability=thinking` is accepted only when a peer advertises it, and even then Grok remains the agent that decides whether to call it.

## Caller identity

Every decision the engine records carries the signals-protocol identity of the caller:

- `caller_project` from `kwargs_json` or the `x-signals-project` gRPC metadata
- `caller_agent` from `kwargs_json` or `x-signals-agent`
- `run_id` and `tx_id` from `RunRequest`

Those fields are properties on the decision node. The warehouse mutation callback upserts that node through Postgres and impala_fdw into Kudu. Signals settles Kudu into Iceberg. Querying the foreign tables covers both tiers. This process does not write Iceberg, and a restart does not read the Kudu rows back into the Explorer graph.

## What the workspace shows

The chain is the causal walk (`CAUSED`, `INFLUENCED`, `PRECEDENT_FOR`), not the hop neighborhood. Explainability is the upstream list plus the downstream list. Precedents stay the lexical comparison already on `GET /api/decisions/{id}/precedents`. The run box posts to `POST /api/agent/runs`, which dials the local engine. Peers should call `Agents/Run` themselves.

The engine authenticates to the HTTP API with `SEMANTICA_API_KEY` when that variable is set on both processes. A browser login cookie is not something the engine has.
