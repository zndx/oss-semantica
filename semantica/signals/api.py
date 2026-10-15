"""HTTP client the lattice engine uses to reach Semantica.

The engine does not share the Explorer process, so ontology search,
forward chaining, ingest, and decision writes go through the API. An
API key on both processes is how this client authenticates. A browser
login cookie is not available to the engine.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def http_base() -> str:
    return (os.environ.get("SEMANTICA_HTTP_URL") or "http://127.0.0.1:8070").rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = (os.environ.get("SEMANTICA_API_KEY") or "").strip()
    if key:
        headers["X-API-Key"] = key
    return headers


class SemanticaApi:
    """Small urllib client. Tests pass a stand-in with the same methods."""

    def __init__(self, base: str | None = None, timeout: float = 60.0) -> None:
        self.base = (base or http_base()).rstrip("/")
        self.timeout = timeout

    def call(self, method: str, path: str, payload: dict | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers=_headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc
        if not raw:
            return None
        return json.loads(raw.decode())

    def search_nodes(self, query: str, limit: int = 8, node_type: str | None = None) -> Any:
        filters = {"type": node_type} if node_type else {}
        return self.call(
            "POST",
            "/api/graph/search",
            {"query": query, "limit": limit, "filters": filters},
        )

    def get_node(self, node_id: str) -> Any:
        quoted = urllib.request.quote(node_id, safe="")
        return self.call("GET", f"/api/graph/node?node_id={quoted}")

    def forward_chain(self, facts: list[str], rules: list[str], apply_to_graph: bool = False) -> Any:
        return self.call(
            "POST",
            "/api/reason",
            {
                "facts": facts,
                "rules": rules,
                "mode": "forward",
                "apply_to_graph": apply_to_graph,
            },
        )

    def load_ontology(self, content: str, fmt: str, name: str) -> Any:
        return self.call(
            "POST",
            "/api/ontology/load",
            {"content": content, "format": fmt, "name": name},
        )

    def record_decision(self, payload: dict) -> Any:
        return self.call("POST", "/api/decisions", payload)

    def link_decisions(self, source_id: str, target_id: str, relationship: str) -> Any:
        return self.call(
            "POST",
            "/api/decisions/link",
            {
                "source_id": source_id,
                "target_id": target_id,
                "relationship": relationship,
            },
        )

    def find_precedents(self, scenario: str, limit: int = 5) -> Any:
        quoted = urllib.request.quote(scenario, safe="")
        return self.call("GET", f"/api/decisions/precedents?scenario={quoted}&limit={limit}")

    def explain(self, decision_id: str) -> Any:
        quoted = urllib.request.quote(decision_id, safe="")
        return self.call("GET", f"/api/decisions/{quoted}/explain")
