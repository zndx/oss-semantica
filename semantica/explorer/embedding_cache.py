"""IRI-keyed dense vectors for the Explorer semantic metric.

The HTTP process only reads a NumPy archive. It never imports torch.
``scripts/embed_sdg_gemma.py`` writes the archive: one sentence-transformers
process per GPU, ``google/embeddinggemma-2``, prompt ``SentenceSimilarity``.

Node ids are the IRIs the ontology and SKOS loaders assign. A vector already
stored on the node wins. IRIs that are not in the archive simply have no score.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

MODEL_ID = "google/embeddinggemma-2"
PROMPT_NAME = "SentenceSimilarity"
DIM = 768
_ID_WIDTH = 1024
_DEFAULT_RELATIVE = Path(".devenv/state/embeddings/sdg-embeddinggemma-2.npz")

_lock = threading.Lock()
# (stamp, id -> float32 row). The row is a view into one matrix.
_loaded: Optional[tuple[tuple, dict[str, Any]]] = None


def embedding_text(node: Mapping[str, Any]) -> str:
    """Text the encoder and the graph agree on.

    Label, then a comment or description when it says something else, then
    SKOS alt labels that are already stored on the node. Identifier-only
    local names are what remains when a node has no label.
    """
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    label = (
        props.get("rdfs:label")
        or node.get("content")
        or props.get("content")
        or props.get("pref_label")
        or props.get("label")
        or ""
    )
    label = str(label).strip()
    if not label:
        node_id = str(node.get("id") or "")
        local = node_id.rstrip("/").split("/")[-1].split("#")[-1]
        label = local or node_id

    parts = [label]
    comment = (
        props.get("rdfs:comment")
        or props.get("description")
        or node.get("description")
        or ""
    )
    comment = str(comment).strip()
    if comment and comment != label:
        parts.append(comment)

    alts = props.get("alt_labels") or []
    if isinstance(alts, str):
        alts = [alts]
    seen = {label}
    extra: list[str] = []
    for alt in alts:
        text = str(alt).strip()
        if text and text not in seen:
            seen.add(text)
            extra.append(text)
    if extra:
        parts.append(", ".join(extra))
    return "\n".join(parts)


def cache_path() -> Path:
    """Read at call time so a file written after startup is visible."""
    override = os.environ.get("SEMANTICA_EMBEDDING_CACHE")
    if override:
        return Path(override)
    return _DEFAULT_RELATIVE


def cache_stamp(path: Optional[Path] = None) -> Optional[tuple]:
    target = path if path is not None else cache_path()
    try:
        st = target.stat()
    except OSError:
        return None
    if not target.is_file():
        return None
    return (str(target.resolve()), st.st_mtime_ns, st.st_size)


def clear_memory() -> None:
    """Drop the in-process matrix. Tests use this between files."""
    global _loaded
    with _lock:
        _loaded = None


def write_cache(
    path: Path,
    ids: Sequence[str],
    vectors: Any,
    meta: Optional[Mapping[str, Any]] = None,
) -> None:
    """Write ``ids`` and a float32 matrix atomically. Ids longer than 1024 are refused."""
    import numpy as np

    if any(len(str(node_id)) > _ID_WIDTH for node_id in ids):
        raise ValueError(f"embedding id exceeds {_ID_WIDTH} characters")
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(ids):
        raise ValueError(
            f"embedding matrix shape {matrix.shape} does not match {len(ids)} ids"
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial.npz")
    np.savez(
        partial,
        ids=np.asarray([str(node_id) for node_id in ids], dtype=f"U{_ID_WIDTH}"),
        vectors=matrix,
    )
    os.replace(partial, path)
    sidecar = path.with_suffix(".json")
    payload = {
        "model": MODEL_ID,
        "prompt": PROMPT_NAME,
        "dim": int(matrix.shape[1]),
        "normalized": True,
        "count": len(ids),
    }
    if meta:
        payload.update(dict(meta))
    partial_json = sidecar.with_name(f".{sidecar.name}.partial")
    partial_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(partial_json, sidecar)


def _matrix(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    global _loaded
    import numpy as np

    stamp = cache_stamp(path)
    if stamp is None:
        return None
    with _lock:
        if _loaded is not None and _loaded[0] == stamp:
            return _loaded[1]
        target = Path(stamp[0])
        try:
            with np.load(target, allow_pickle=False) as data:
                raw_ids = data["ids"]
                vectors = np.asarray(data["vectors"], dtype=np.float32)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("embedding cache %s is unreadable: %s", target, exc)
            return None
        if vectors.ndim != 2 or len(raw_ids) != vectors.shape[0]:
            logger.warning("embedding cache %s has a mismatched id/vector shape", target)
            return None
        table = {str(node_id): vectors[index] for index, node_id in enumerate(raw_ids.tolist())}
        _loaded = (stamp, table)
        return table


def lookup_vectors(
    node_ids: Iterable[str],
    path: Optional[Path] = None,
) -> dict[str, list[float]]:
    """Vectors for ``node_ids`` that the archive knows. Others are omitted."""
    table = _matrix(path)
    if not table:
        return {}
    found: dict[str, list[float]] = {}
    for node_id in node_ids:
        row = table.get(str(node_id))
        if row is not None:
            found[str(node_id)] = row.tolist()
    return found
