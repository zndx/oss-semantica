"""Stdio MCP server the Grok ACP session uses.

Tools call the Semantica HTTP API. ``lattice_complete`` calls a peer
only when ``SEMANTICA_THINKING_PEER`` names an engine that already
advertises the capability. This process does not load a model.
"""

from __future__ import annotations

import json
import os
import sys

from semantica.signals.api import SemanticaApi


def _tools() -> list[dict]:
    return [
        {
            "name": "search_nodes",
            "description": "Search the loaded Semantica graph.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "node_type": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_node",
            "description": "Fetch one graph node by id.",
            "inputSchema": {
                "type": "object",
                "properties": {"node_id": {"type": "string"}},
                "required": ["node_id"],
            },
        },
        {
            "name": "forward_chain",
            "description": "Run Semantica forward chaining on the supplied facts and rules.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "facts": {"type": "array", "items": {"type": "string"}},
                    "rules": {"type": "array", "items": {"type": "string"}},
                    "apply_to_graph": {"type": "boolean"},
                },
                "required": ["facts", "rules"],
            },
        },
        {
            "name": "load_ontology",
            "description": "Ingest an ontology document into the Explorer graph.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "format": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["content", "format", "name"],
            },
        },
        {
            "name": "record_decision",
            "description": "Store a decision. Include caller_project, run_id, and tx_id when you have them.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "scenario": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "outcome": {"type": "string"},
                    "confidence": {"type": "number"},
                    "caused_by": {"type": "string"},
                    "relationship": {"type": "string"},
                    "caller_project": {"type": "string"},
                    "caller_agent": {"type": "string"},
                    "run_id": {"type": "string"},
                    "tx_id": {"type": "string"},
                    "policy_id": {"type": "string"},
                    "policy_result": {"type": "string"},
                },
                "required": ["category", "scenario", "reasoning", "outcome", "confidence"],
            },
        },
        {
            "name": "link_decisions",
            "description": "Add a CAUSED, INFLUENCED, or PRECEDENT_FOR edge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "target_id": {"type": "string"},
                    "relationship": {"type": "string"},
                },
                "required": ["source_id", "target_id", "relationship"],
            },
        },
        {
            "name": "find_precedents",
            "description": "Find past decisions with similar scenario text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["scenario"],
            },
        },
        {
            "name": "explain_decision",
            "description": "Return the upstream and downstream causal chain.",
            "inputSchema": {
                "type": "object",
                "properties": {"decision_id": {"type": "string"}},
                "required": ["decision_id"],
            },
        },
        {
            "name": "lattice_complete",
            "description": "Call a model capability advertised by a lattice peer. Refuses when none is configured.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "capability": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["capability", "prompt"],
            },
        },
    ]


def _call(api: SemanticaApi, name: str, arguments: dict) -> dict:
    if name == "search_nodes":
        return api.search_nodes(
            arguments["query"],
            int(arguments.get("limit") or 8),
            arguments.get("node_type"),
        )
    if name == "get_node":
        return api.get_node(arguments["node_id"])
    if name == "forward_chain":
        return api.forward_chain(
            list(arguments.get("facts") or []),
            list(arguments.get("rules") or []),
            bool(arguments.get("apply_to_graph")),
        )
    if name == "load_ontology":
        return api.load_ontology(
            arguments["content"], arguments.get("format") or "turtle", arguments.get("name") or "ingested"
        )
    if name == "record_decision":
        payload = dict(arguments)
        payload.setdefault("decision_maker", "acp/grok-build")
        return api.record_decision(payload)
    if name == "link_decisions":
        return api.link_decisions(
            arguments["source_id"],
            arguments["target_id"],
            arguments.get("relationship") or "CAUSED",
        )
    if name == "find_precedents":
        return api.find_precedents(arguments["scenario"], int(arguments.get("limit") or 5))
    if name == "explain_decision":
        return api.explain(arguments["decision_id"])
    if name == "lattice_complete":
        peer = (os.environ.get("SEMANTICA_THINKING_PEER") or "").strip()
        capability = arguments.get("capability") or ""
        if not peer:
            return {"error": f"{capability or 'capability'} is not advertised on the lattice"}
        from signals_engine.client import complete
        from signals_engine.types import CompletionRequest

        result = complete(
            peer,
            CompletionRequest(capability=capability, prompt=arguments.get("prompt") or ""),
        )
        return {"text": result.text, "model": result.model, "peer": peer}
    return {"error": f"unknown tool {name}"}


def _handle(api: SemanticaApi, message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "semantica", "version": "0.7.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _tools()}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            payload = _call(api, params.get("name") or "", params.get("arguments") or {})
            text = json.dumps(payload)
            is_error = isinstance(payload, dict) and "error" in payload
        except Exception as exc:
            text = str(exc)
            is_error = True
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
        }
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def _read_message() -> dict | None:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode())


def _write(message: dict) -> None:
    raw = json.dumps(message).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    sys.stdout.buffer.flush()


def main() -> None:
    api = SemanticaApi()
    while True:
        message = _read_message()
        if message is None:
            return
        response = _handle(api, message)
        if response is not None:
            _write(response)


if __name__ == "__main__":
    main()
