"""Upsert the in-memory graph into Kudu through the local Postgres FDW."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

log = logging.getLogger("semantica.warehouse")

_INSTALL_HINT = (
    "psycopg is not installed. Install the warehouse extra: "
    "pip install 'semantica[warehouse]'"
)


class WarehouseGraph:
    """Mutation callback. INSERT on the tier0 foreign table is a Kudu upsert.

    ``REMOVE_*`` writes ``removed = 1``. The FDW does not DELETE rows.
    ``warehouse.v_node`` and ``warehouse.v_edge`` keep ``removed = 0``.
    The flag is an integer because ``kudu_scan`` cannot push a boolean NOT.
    """

    def __init__(self, dsn: str, graph: Any) -> None:
        self.dsn = dsn
        self.graph = graph
        self._lock = threading.Lock()
        self._conn: Any = None

    def close(self) -> None:
        with self._lock:
            conn = self._conn
            self._conn = None
        if conn is not None and not conn.closed:
            conn.close()

    def __call__(self, operation: str, entity_id: str, payload: dict) -> None:
        if not isinstance(payload, dict):
            payload = {}
        graph_id = str(getattr(self.graph, "graph_id", "") or "")
        if not graph_id:
            return
        # 0/1, not a boolean. kudu_scan cannot push `NOT removed`.
        removed = 1 if operation in {"REMOVE_NODE", "REMOVE_EDGE"} else 0
        now = time.time_ns()
        if operation in {"ADD_NODE", "UPDATE_NODE", "REMOVE_NODE"}:
            self._exec(
                """
                INSERT INTO semantica_node_tier0 (
                  graph_id, node_id, node_type, label, properties, updated_ns, removed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                _node_row(graph_id, entity_id, payload, removed, now),
            )
        elif operation in {"ADD_EDGE", "UPDATE_EDGE", "REMOVE_EDGE"}:
            self._exec(
                """
                INSERT INTO semantica_edge_tier0 (
                  graph_id, edge_id, source_id, target_id, edge_type,
                  weight, properties, updated_ns, removed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                _edge_row(graph_id, entity_id, payload, removed, now),
            )

    def register_graph(self, name: str = "") -> None:
        graph_id = str(getattr(self.graph, "graph_id", "") or "")
        if not graph_id:
            return
        self._exec(
            """
            INSERT INTO admin.graph (graph_id, name)
            VALUES (%s, %s)
            ON CONFLICT (graph_id) DO NOTHING
            """,
            (graph_id, name or ""),
        )

    def probe(self) -> None:
        """Fail startup when the tier0 foreign table is not installed."""
        self._exec("SELECT 1 FROM semantica_node_tier0 LIMIT 0", ())

    def _exec(self, sql: str, params: tuple) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, params)
                conn.commit()
            except Exception:
                conn.rollback()
                self._conn = None
                conn.close()
                raise

    def _connect(self):
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(_INSTALL_HINT) from exc
        self._conn = psycopg.connect(self.dsn)
        return self._conn


def attach_warehouse(graph: Any, dsn: str) -> WarehouseGraph:
    """Register ``graph`` in ``admin.graph`` and store later mutations in Kudu."""
    writer = WarehouseGraph(dsn, graph)
    writer.register_graph()
    writer.probe()
    previous = getattr(graph, "mutation_callback", None)

    def chained(operation: str, entity_id: str, payload: dict) -> None:
        writer(operation, entity_id, payload)
        if callable(previous):
            previous(operation, entity_id, payload)

    graph.mutation_callback = chained
    log.info("warehouse graph %s → Kudu tier0 via %s", graph.graph_id, _dsn_host(dsn))
    return writer


def _dsn_host(dsn: str) -> str:
    return dsn.split("@")[-1] if "@" in dsn else dsn


def _node_row(graph_id: str, entity_id: str, payload: dict, removed: int, now: int) -> tuple:
    props = payload.get("properties")
    if not isinstance(props, dict):
        props = {}
    content = props.get("content")
    label = content if isinstance(content, str) else ""
    return (
        graph_id,
        str(payload.get("id") or entity_id),
        str(payload.get("type") or ""),
        label[:500],
        json.dumps(props, default=str),
        now,
        removed,
    )


def _edge_row(graph_id: str, entity_id: str, payload: dict, removed: int, now: int) -> tuple:
    props = payload.get("properties")
    if not isinstance(props, dict):
        props = {}
    weight = payload.get("weight", 1.0)
    try:
        weight_f = float(weight)
    except (TypeError, ValueError):
        weight_f = 1.0
    return (
        graph_id,
        str(payload.get("id") or entity_id),
        str(payload.get("source_id") or ""),
        str(payload.get("target_id") or ""),
        str(payload.get("type") or ""),
        weight_f,
        json.dumps(props, default=str),
        now,
        removed,
    )
