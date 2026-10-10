"""
Vocabulary routes for SKOS scheme discovery, hierarchy browsing, and import.
"""

import asyncio
import os
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.datastructures import UploadFile

from ..dependencies import get_session
from ..schemas import ConceptNode, ConceptSummary, VocabularyImportResponse, VocabularyScheme
from ..session import GraphSession
from ..utils.rdf_parser import parse_skos_file

router = APIRouter(prefix="/api/vocabulary", tags=["Vocabulary"])

# Read at import. Default stays 10 MB. SEMANTICA_MAX_VOCABULARY_BYTES raises it.
_MAX_UPLOAD_BYTES = int(os.environ.get("SEMANTICA_MAX_VOCABULARY_BYTES", str(10 * 1024 * 1024)))
_ALLOWED_EXTENSIONS = frozenset({".ttl", ".rdf", ".owl", ".xml", ".jsonld", ".json-ld", ".json"})


def _concept_summary(node: dict, scheme_uri: Optional[str] = None, parent_uri: Optional[str] = None) -> ConceptSummary:
    properties = node.get("properties", {})
    alt_labels = properties.get("alt_labels") or []
    if isinstance(alt_labels, str):
        alt_labels = [alt_labels]
    return ConceptSummary(
        uri=node.get("id", ""),
        pref_label=properties.get("pref_label") or properties.get("content", node.get("content", node.get("id", ""))),
        alt_labels=list(alt_labels),
        description=properties.get("description"),
        notation=properties.get("notation"),
        scheme_uri=scheme_uri,
        parent_uri=parent_uri,
    )


_CROSS_FILE_LINKS = (
    "skos:broader",
    "skos:narrower",
    "skos:related",
    "skos:exactMatch",
    "skos:closeMatch",
    "skos:broadMatch",
    "skos:narrowMatch",
    "skos:relatedMatch",
)


def _link_existing_targets(nodes: List[dict], edges: List[dict], session: GraphSession) -> None:
    """Turn a stored link into an edge when the other node is already loaded.

    The parser refuses an edge whose other end is not in the same file, so a
    later file cannot invent a stub. If that node was loaded earlier, the
    canvas can draw the link. A target that is still absent stays on the
    node only.
    """
    declared = {str(node.get("id")) for node in nodes if node.get("id")}
    seen = {
        (edge.get("source_id"), edge.get("target_id"), edge.get("type"))
        for edge in edges
    }

    def _add(source: str, target: str, edge_type: str) -> None:
        if not target or target in declared:
            return
        key = (source, target, edge_type)
        if key in seen or session.get_node(target) is None:
            return
        edges.append({
            "source_id": source,
            "target_id": target,
            "type": edge_type,
            "weight": 1.0,
            "properties": {},
        })
        seen.add(key)

    for node in nodes:
        source = str(node.get("id") or "")
        if not source:
            continue
        props = node.get("properties") or {}
        _add(source, str(props.get("scheme_uri") or ""), "skos:inScheme")
        for edge_type in _CROSS_FILE_LINKS:
            for target in _uri_list(props.get(edge_type)):
                _add(source, target, edge_type)


def _uri_list(value: object) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return [str(value)]


def _collect_scheme_members(
    edges: List[dict],
    scheme_uri: str,
    nodes: Optional[List[dict]] = None,
) -> set[str]:
    members: set[str] = set()
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        edge_type = edge.get("type")
        if target == scheme_uri and edge_type in {"skos:inScheme", "skos:topConceptOf"}:
            members.add(source)
        elif source == scheme_uri and edge_type == "skos:hasTopConcept":
            members.add(target)
    for node in nodes or []:
        # A concept imported from a file that does not restate the scheme
        # still names it in properties.scheme_uri.
        node_id = node.get("id")
        if node_id and (node.get("properties") or {}).get("scheme_uri") == scheme_uri:
            members.add(node_id)
    return members


def _collect_parent_map(
    member_ids: set[str],
    edges: List[dict],
    nodes: Optional[List[dict]] = None,
) -> Dict[str, str]:
    parent_by_child: Dict[str, str] = {}
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        edge_type = edge.get("type")
        if source not in member_ids or target not in member_ids:
            continue
        if edge_type == "skos:broader":
            parent_by_child.setdefault(source, target)
        elif edge_type == "skos:narrower":
            parent_by_child.setdefault(target, source)
    for node in nodes or []:
        node_id = node.get("id")
        if node_id not in member_ids:
            continue
        props = node.get("properties") or {}
        for parent in _uri_list(props.get("skos:broader")):
            if parent in member_ids and parent != node_id:
                parent_by_child.setdefault(node_id, parent)
        for child in _uri_list(props.get("skos:narrower")):
            if child in member_ids and child != node_id:
                parent_by_child.setdefault(child, node_id)
    return parent_by_child


def _build_hierarchy(concepts: Dict[str, ConceptSummary], parent_by_child: Dict[str, str]) -> List[ConceptNode]:
    children_by_parent: Dict[str, List[str]] = defaultdict(list)
    for child_uri, parent_uri in parent_by_child.items():
        if child_uri != parent_uri:
            children_by_parent[parent_uri].append(child_uri)

    def attach(uri: str, trail: set[str]) -> ConceptNode:
        concept = concepts[uri]
        child_nodes = [
            attach(child_uri, trail | {uri})
            for child_uri in sorted(children_by_parent.get(uri, []))
            if child_uri not in trail
        ]
        return ConceptNode(
            uri=concept.uri,
            pref_label=concept.pref_label,
            alt_labels=concept.alt_labels,
            description=concept.description,
            notation=concept.notation,
            scheme_uri=concept.scheme_uri,
            parent_uri=concept.parent_uri,
            children=child_nodes or None,
        )

    root_ids = [uri for uri in sorted(concepts.keys()) if uri not in parent_by_child]
    if not root_ids:
        root_ids = sorted(concepts.keys())
    return [attach(uri, {uri}) for uri in root_ids]


@router.get("/schemes", response_model=List[VocabularyScheme])
async def list_schemes(
    session: GraphSession = Depends(get_session),
):
    nodes, _ = await asyncio.to_thread(
        session.get_nodes,
        node_type="skos:ConceptScheme",
        skip=0,
        limit=999_999,
    )
    return [
        VocabularyScheme(
            uri=node.get("id", ""),
            label=node.get("properties", {}).get("content", node.get("content", node.get("id", ""))),
            description=node.get("properties", {}).get("description"),
        )
        for node in nodes
    ]


@router.get("/concepts", response_model=List[ConceptSummary])
async def list_concepts(
    scheme: str = Query(..., description="ConceptScheme URI"),
    search: Optional[str] = Query(None, description="Filter concepts by label or metadata"),
    session: GraphSession = Depends(get_session),
):
    nodes, _ = await asyncio.to_thread(
        session.get_nodes,
        node_type="skos:Concept",
        search=search,
        skip=0,
        limit=999_999,
    )
    edges, _ = await asyncio.to_thread(session.get_edges, skip=0, limit=999_999)

    member_ids = _collect_scheme_members(edges, scheme, nodes)
    parent_by_child = _collect_parent_map(member_ids, edges, nodes)

    concepts = []
    for node in nodes:
        node_id = node.get("id")
        if node_id not in member_ids:
            continue
        concepts.append(_concept_summary(node, scheme_uri=scheme, parent_uri=parent_by_child.get(node_id)))

    return sorted(concepts, key=lambda concept: (concept.pref_label.lower(), concept.uri))


@router.get("/hierarchy", response_model=List[ConceptNode])
async def get_hierarchy(
    scheme: str = Query(..., description="ConceptScheme URI"),
    session: GraphSession = Depends(get_session),
):
    nodes, _ = await asyncio.to_thread(
        session.get_nodes,
        node_type="skos:Concept",
        skip=0,
        limit=999_999,
    )
    edges, _ = await asyncio.to_thread(session.get_edges, skip=0, limit=999_999)

    member_ids = _collect_scheme_members(edges, scheme, nodes)
    parent_by_child = _collect_parent_map(member_ids, edges, nodes)

    concepts: Dict[str, ConceptSummary] = {}
    for node in nodes:
        node_id = node.get("id")
        if node_id not in member_ids:
            continue
        concepts[node_id] = _concept_summary(
            node,
            scheme_uri=scheme,
            parent_uri=parent_by_child.get(node_id),
        )

    return _build_hierarchy(concepts, parent_by_child)


@router.post("/import", response_model=VocabularyImportResponse)
async def import_vocabulary(
    request: Request,
    session: GraphSession = Depends(get_session),
):
    # File() / Form() parse the body before this route runs, and Starlette's
    # default part cap is 1 MB. vocabulary.ttl is larger than that and smaller
    # than SEMANTICA_MAX_VOCABULARY_BYTES, so the cap has to be applied here.
    form = await request.form(max_part_size=_MAX_UPLOAD_BYTES)
    upload = form.get("file")
    text = form.get("text")
    format = form.get("format")
    file = upload if isinstance(upload, UploadFile) else None
    if isinstance(text, UploadFile):
        text = (await text.read()).decode("utf-8", errors="replace")
    if isinstance(format, UploadFile):
        format = (await format.read()).decode("utf-8", errors="replace")
    text = text if isinstance(text, str) else None
    format = format if isinstance(format, str) else None

    if file is None and not text:
        raise HTTPException(status_code=422, detail="Provide either a vocabulary file or raw RDF text.")

    filename = file.filename if file else None
    if file is not None:
        if filename:
            ext = os.path.splitext(filename.lower())[1]
            if ext not in _ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported file type '{ext}'. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
                )
        content = await file.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Upload exceeds the {_MAX_UPLOAD_BYTES} byte limit; "
                    "set SEMANTICA_MAX_VOCABULARY_BYTES to raise the limit."
                ),
            )
    else:
        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Text payload exceeds the {_MAX_UPLOAD_BYTES} byte limit; "
                    "set SEMANTICA_MAX_VOCABULARY_BYTES to raise the limit."
                ),
            )
        content = encoded

    parse_format = (format or "").strip().lower() or None
    if parse_format is None:
        if filename:
            lower_name = filename.lower()
            if lower_name.endswith((".rdf", ".owl", ".xml")):
                parse_format = "xml"
            elif lower_name.endswith((".jsonld", ".json-ld", ".json")):
                parse_format = "json-ld"
            else:
                parse_format = "turtle"
        else:
            parse_format = "turtle"

    try:
        nodes, edges = await asyncio.to_thread(parse_skos_file, content, parse_format)
        await asyncio.to_thread(_link_existing_targets, nodes, edges, session)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        nodes_added, edges_added = await asyncio.to_thread(
            session.add_nodes_and_edges, nodes, edges
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return VocabularyImportResponse(
        status="success",
        filename=filename,
        nodes_added=nodes_added,
        edges_added=edges_added,
        format=parse_format,
    )
