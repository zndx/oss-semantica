"""
Decision routes using ContextGraph-native fallbacks.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_session
from ..schemas import (
    CausalChainResponse,
    CausalDistanceReport,
    ComplianceResponse,
    DecisionExplainResponse,
    DecisionResponse,
    LinkDecisionsRequest,
    RecordDecisionRequest,
)
from ..session import GraphSession

router = APIRouter(prefix="/api/decisions", tags=["Decisions"])


_CAUSAL_TYPES = {"CAUSED", "INFLUENCED", "PRECEDENT_FOR", "CAUSES", "INFLUENCES", "PRECEDES"}
_POLICY_RESULTS = {"checked", "passed", "blocked", "exception"}


def _chain_step(decision) -> dict:
    metadata = dict(getattr(decision, "metadata", None) or {})
    return {
        "id": decision.decision_id,
        "type": decision.category,
        "content": decision.outcome or decision.scenario,
        "relationship": metadata.get("causal_edge") or "causal",
        "hop": metadata.get("causal_distance"),
        "scenario": decision.scenario,
        "reasoning": decision.reasoning,
        "outcome": decision.outcome,
        "confidence": decision.confidence,
        "decision_maker": decision.decision_maker,
        "caller_project": metadata.get("caller_project"),
        "run_id": metadata.get("run_id"),
        "tx_id": metadata.get("tx_id"),
    }


def _annotate_edges(graph, focus_id: str, steps: list[dict]) -> list[dict]:
    """Name the causal edge that joins each step to the next, or to the focus."""
    edges = list(getattr(graph, "edges", []) or [])

    def between(left: str, right: str) -> str:
        for edge in edges:
            pair = {getattr(edge, "source_id", None), getattr(edge, "target_id", None)}
            if pair == {left, right}:
                kind = str(getattr(edge, "edge_type", "") or "")
                if kind.upper() in _CAUSAL_TYPES:
                    return kind
        return "causal"

    ids = [step["id"] for step in steps] + [focus_id]
    for index, step in enumerate(steps):
        step["relationship"] = between(ids[index], ids[index + 1])
    return steps


def _node_to_decision(node: dict) -> DecisionResponse:
    properties = node.get("properties", {})
    return DecisionResponse(
        decision_id=node.get("id", ""),
        category=properties.get("category", ""),
        scenario=properties.get("scenario", ""),
        reasoning=properties.get("reasoning", ""),
        outcome=properties.get("outcome", ""),
        confidence=float(properties.get("confidence", 0.0) or 0.0),
        timestamp=properties.get("timestamp"),
        metadata=properties,
    )


@router.get("", response_model=list[DecisionResponse])
async def list_decisions(
    category: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    session: GraphSession = Depends(get_session),
):
    nodes, _ = await asyncio.to_thread(
        session.get_nodes,
        node_type="decision",
        skip=0,
        limit=999_999,
    )

    if category:
        nodes = [
            node
            for node in nodes
            if str(node.get("properties", {}).get("category", "")).lower() == category.lower()
        ]

    return [_node_to_decision(node) for node in nodes[skip : skip + limit]]


@router.get("/causal-distance", response_model=CausalDistanceReport)
async def causal_distance(
    source: str = Query(..., description="Source node/decision ID"),
    target: str = Query(..., description="Target node/decision ID"),
    session: GraphSession = Depends(get_session),
):
    """FR-8 — Compute causal distance between two decisions via causal-edge-only traversal."""
    from ...context.causal_analyzer import CausalChainAnalyzer

    analyzer = CausalChainAnalyzer(session.graph)
    report = await asyncio.to_thread(analyzer.interpret_causal_distance, source, target)
    return CausalDistanceReport(**report)


# NOTE: static routes (e.g. /causal-distance above) must stay above this
# dynamic route — Starlette matches in definition order, otherwise the static
# path is captured as decision_id (see issue #1531).
@router.get("/precedents", response_model=list[DecisionResponse])
async def search_precedents(
    scenario: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
    session: GraphSession = Depends(get_session),
):
    """Lexical precedents for a scenario. Static path, above ``/{decision_id}``."""
    found = await asyncio.to_thread(
        session.graph.find_precedents_by_scenario,
        scenario,
        limit=limit,
        similarity_threshold=0.05,
    )
    rows = []
    for item in found:
        decision = item.get("decision") or {}
        decision_id = decision.get("id") or decision.get("decision_id") or ""
        node = session.get_node(decision_id) if decision_id else None
        if node is not None:
            row = _node_to_decision(node)
            row.metadata = {**row.metadata, "similarity": item.get("similarity")}
            rows.append(row)
    return rows


@router.get("/{decision_id}", response_model=DecisionResponse)
async def get_decision(
    decision_id: str,
    session: GraphSession = Depends(get_session),
):
    node = await asyncio.to_thread(session.get_node, decision_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")
    return _node_to_decision(node)


@router.get("/{decision_id}/chain", response_model=CausalChainResponse)
async def get_causal_chain(
    decision_id: str,
    direction: str = Query("upstream"),
    max_depth: int = Query(5, ge=1, le=20),
    session: GraphSession = Depends(get_session),
):
    """Walk CAUSED / INFLUENCED / PRECEDENT_FOR edges, not ordinary neighbors."""
    if direction not in {"upstream", "downstream"}:
        raise HTTPException(status_code=422, detail="direction must be upstream or downstream")
    node = await asyncio.to_thread(session.get_node, decision_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")

    from ...context.causal_analyzer import CausalChainAnalyzer

    analyzer = CausalChainAnalyzer(session.graph)
    decisions = await asyncio.to_thread(
        analyzer.get_causal_chain, decision_id, direction, max_depth
    )
    steps = _annotate_edges(session.graph, decision_id, [_chain_step(item) for item in decisions])
    return CausalChainResponse(decision_id=decision_id, direction=direction, chain=steps)


@router.get("/{decision_id}/explain", response_model=DecisionExplainResponse)
async def explain_decision(
    decision_id: str,
    max_depth: int = Query(5, ge=1, le=20),
    session: GraphSession = Depends(get_session),
):
    """Upstream causes and downstream effects for one decision.

    Computed on the Explorer graph. This does not build an AgentContext
    or a second vector store.
    """
    node = await asyncio.to_thread(session.get_node, decision_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")

    from ...context.causal_analyzer import CausalChainAnalyzer

    analyzer = CausalChainAnalyzer(session.graph)

    def _both():
        upstream = analyzer.get_causal_chain(decision_id, "upstream", max_depth)
        downstream = analyzer.get_causal_chain(decision_id, "downstream", max_depth)
        return upstream, downstream

    upstream, downstream = await asyncio.to_thread(_both)
    up_steps = _annotate_edges(session.graph, decision_id, [_chain_step(item) for item in upstream])
    down_steps = _annotate_edges(session.graph, decision_id, [_chain_step(item) for item in downstream])
    return DecisionExplainResponse(
        decision_id=decision_id,
        upstream=up_steps,
        downstream=down_steps,
        total_connections=len(up_steps) + len(down_steps),
    )


@router.post("", response_model=DecisionResponse)
async def record_decision(
    body: RecordDecisionRequest,
    session: GraphSession = Depends(get_session),
):
    """Record a decision on the Explorer graph.

    ``record_decision`` writes a ``decision`` node on the Explorer graph.
    The lattice engine reaches this route over HTTP. It does not open a
    warehouse connection. When the warehouse client is attached, the
    mutation callback writes the node to Postgres, and Postgres sends the
    insert through impala_fdw to the warehouse. Iceberg settle belongs to
    Signals. This process does not write Iceberg and does not reload the
    warehouse on startup.
    """
    if body.policy_result and body.policy_result not in _POLICY_RESULTS:
        raise HTTPException(
            status_code=422,
            detail=f"policy_result must be one of {sorted(_POLICY_RESULTS)}",
        )
    metadata = {
        key: value
        for key, value in {
            "caller_project": body.caller_project,
            "caller_agent": body.caller_agent,
            "run_id": body.run_id,
            "tx_id": body.tx_id,
            "policy_id": body.policy_id,
            "policy_result": body.policy_result,
        }.items()
        if value
    }
    outcome = "blocked" if body.policy_result == "blocked" else body.outcome

    def _write() -> str:
        decision_id = session.graph.record_decision(
            category=body.category,
            scenario=body.scenario,
            reasoning=body.reasoning,
            outcome=outcome,
            confidence=body.confidence,
            entities=body.entities,
            decision_maker=body.decision_maker,
            metadata=metadata,
        )
        if body.caused_by:
            session.graph.add_causal_relationship(
                body.caused_by, decision_id, body.relationship
            )
        return decision_id

    try:
        decision_id = await asyncio.to_thread(_write)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    node = await asyncio.to_thread(session.get_node, decision_id)
    if node is None:
        raise HTTPException(status_code=500, detail="Decision was not stored.")
    return _node_to_decision(node)


@router.post("/link")
async def link_decisions(
    body: LinkDecisionsRequest,
    session: GraphSession = Depends(get_session),
):
    def _link() -> bool:
        return session.graph.add_causal_relationship(
            body.source_id, body.target_id, body.relationship
        )

    try:
        linked = await asyncio.to_thread(_link)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not linked:
        raise HTTPException(
            status_code=404,
            detail="Both endpoints must be decision nodes that are already stored.",
        )
    return {"source_id": body.source_id, "target_id": body.target_id, "relationship": body.relationship}


@router.get("/{decision_id}/precedents", response_model=list[DecisionResponse])
async def get_precedents(
    decision_id: str,
    limit: int = Query(10, ge=1, le=100),
    session: GraphSession = Depends(get_session),
):
    node = await asyncio.to_thread(session.get_node, decision_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")

    properties = node.get("properties", {})
    category = str(properties.get("category", ""))
    scenario_words = set(str(properties.get("scenario", "")).lower().split())

    all_decisions, _ = await asyncio.to_thread(
        session.get_nodes,
        node_type="decision",
        skip=0,
        limit=999_999,
    )

    scored = []
    for decision in all_decisions:
        if decision.get("id") == decision_id:
            continue
        other_props = decision.get("properties", {})
        score = 0.0
        if category and str(other_props.get("category", "")).lower() == category.lower():
            score += 0.5
        other_words = set(str(other_props.get("scenario", "")).lower().split())
        if scenario_words and other_words:
            overlap = len(scenario_words & other_words) / max(len(scenario_words | other_words), 1)
            score += 0.5 * overlap
        scored.append((score, decision))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [_node_to_decision(decision) for _, decision in scored[:limit]]


@router.get("/{decision_id}/compliance", response_model=ComplianceResponse)
async def check_compliance(
    decision_id: str,
    session: GraphSession = Depends(get_session),
):
    node = await asyncio.to_thread(session.get_node, decision_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")

    edges, _ = await asyncio.to_thread(session.get_edges, skip=0, limit=999_999)
    violation_types = {"violates", "non_compliant", "breaches"}
    violations = [
        {
            "policy_id": edge.get("target"),
            "type": edge.get("type"),
            "metadata": edge.get("properties", {}),
        }
        for edge in edges
        if edge.get("source") == decision_id and edge.get("type") in violation_types
    ]

    return ComplianceResponse(
        decision_id=decision_id,
        compliant=len(violations) == 0,
        violations=violations,
    )
