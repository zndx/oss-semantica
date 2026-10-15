"""Causal chain reads decision edges, and a record keeps the caller ids."""

import os

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from semantica.context.context_graph import ContextGraph
from semantica.explorer.app import create_app
from semantica.explorer.session import GraphSession


def _client(graph: ContextGraph) -> TestClient:
    return TestClient(create_app(session=GraphSession(graph)))


def _pair(graph: ContextGraph) -> tuple[str, str]:
    first = graph.record_decision(
        category="classification",
        scenario="Cluster overlaps a known pattern",
        reasoning="The overlap is specific enough to classify.",
        outcome="classified",
        confidence=0.9,
        decision_maker="fixture",
    )
    second = graph.record_decision(
        category="escalation",
        scenario="Classification crossed the threshold",
        reasoning="The earlier classification caused this escalation.",
        outcome="escalated",
        confidence=0.95,
        decision_maker="fixture",
    )
    graph.add_causal_relationship(first, second, "CAUSED")
    graph.add_node("http://example.org/Class", "owl:Class", content="Not a decision")
    graph.add_edge(second, "http://example.org/Class", "rdfs:seeAlso")
    return first, second


def test_chain_returns_the_causing_decision_not_the_class_neighbor():
    graph = ContextGraph(advanced_analytics=False)
    first, second = _pair(graph)
    with _client(graph) as client:
        response = client.get(f"/api/decisions/{second}/chain")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["direction"] == "upstream"
    assert [step["id"] for step in payload["chain"]] == [first]
    assert payload["chain"][0]["relationship"].upper() == "CAUSED"
    assert "http://example.org/Class" not in {step["id"] for step in payload["chain"]}


def test_explain_counts_both_directions():
    graph = ContextGraph(advanced_analytics=False)
    first, second = _pair(graph)
    with _client(graph) as client:
        response = client.get(f"/api/decisions/{first}/explain")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [step["id"] for step in body["downstream"]] == [second]
    assert body["upstream"] == []
    assert body["total_connections"] == 1


def test_record_keeps_caller_ids_and_notifies_the_warehouse_callback():
    seen = []

    def callback(operation, entity_id, payload):
        seen.append((operation, entity_id, payload))

    graph = ContextGraph(advanced_analytics=False)
    graph.mutation_callback = callback
    with _client(graph) as client:
        response = client.post("/api/decisions", json={
            "category": "ontology_request",
            "scenario": "Gaius asked for a forward-chain over the loaded ontology",
            "reasoning": "The request names a lattice caller and a run.",
            "outcome": "accepted",
            "confidence": 0.8,
            "decision_maker": "acp/grok-build",
            "caller_project": "gaius",
            "caller_agent": "health",
            "run_id": "run-1",
            "tx_id": "tx-1",
            "policy_result": "checked",
            "policy_id": "audit",
        })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"]["caller_project"] == "gaius"
    assert body["metadata"]["run_id"] == "run-1"
    assert body["metadata"]["tx_id"] == "tx-1"
    assert any(operation == "ADD_NODE" and payload.get("type") == "decision" for operation, _, payload in seen)


def test_blocked_policy_is_stored():
    graph = ContextGraph(advanced_analytics=False)
    with _client(graph) as client:
        response = client.post("/api/decisions", json={
            "category": "ontology_request",
            "scenario": "A low-confidence classification",
            "reasoning": "Confidence is below the gate.",
            "outcome": "classified",
            "confidence": 0.2,
            "policy_result": "blocked",
        })
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "blocked"


def test_precedent_search_is_not_captured_as_a_decision_id():
    graph = ContextGraph(advanced_analytics=False)
    graph.record_decision(
        category="classification",
        scenario="Laboratory information management specimen lifecycle",
        reasoning="The scenario names the laboratory domain.",
        outcome="classified",
        confidence=0.9,
    )
    with _client(graph) as client:
        response = client.get(
            "/api/decisions/precedents",
            params={"scenario": "laboratory information management"},
        )
    assert response.status_code == 200, response.text
    assert response.json()[0]["outcome"] == "classified"


def test_agent_run_proxy_does_not_dial_the_live_engine(monkeypatch):
    monkeypatch.setenv("SEMANTICA_ENGINE_TARGET", "127.0.0.1:1")
    graph = ContextGraph(advanced_analytics=False)
    with _client(graph) as client:
        response = client.post("/api/agent/runs", json={"instruction": "do not call grok"})
    assert response.status_code in {502, 503}
    os.environ.pop("SEMANTICA_ENGINE_TARGET", None)
