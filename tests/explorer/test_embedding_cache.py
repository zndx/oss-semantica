"""IRI embedding archive. No model download and no GPU."""

from __future__ import annotations

import numpy as np
import pytest

from semantica.context import ContextGraph
from semantica.explorer.embedding_cache import (
    cache_stamp,
    clear_memory,
    embedding_text,
    lookup_vectors,
    write_cache,
)
from semantica.explorer.session import GraphSession


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("SEMANTICA_EMBEDDING_CACHE", raising=False)
    clear_memory()
    yield
    clear_memory()


def test_embedding_text_uses_label_and_a_different_comment():
    text = embedding_text(
        {
            "id": "https://signals.zndx.org/sdg/Class",
            "content": "Class",
            "properties": {
                "rdfs:label": "Sustainable city",
                "rdfs:comment": "A settlement that meets the goal.",
            },
        }
    )
    assert text == "Sustainable city\nA settlement that meets the goal."


def test_embedding_text_falls_back_to_the_local_name():
    text = embedding_text({"id": "https://example.org/ontology#BFO_0000001", "properties": {}})
    assert text == "BFO_0000001"


def test_embedding_text_includes_stored_alt_labels_once():
    text = embedding_text(
        {
            "id": "https://signals.zndx.org/sdg/concept",
            "properties": {
                "content": "poverty",
                "description": "lack of resources",
                "alt_labels": ["poverty", "destitution"],
            },
        }
    )
    assert text == "poverty\nlack of resources\ndestitution"


def test_archive_roundtrip_and_session_merge(tmp_path, monkeypatch):
    path = tmp_path / "nodes.npz"
    ids = ["https://ex.org/a", "https://ex.org/b"]
    vectors = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    write_cache(path, ids, vectors, {"model": "google/embeddinggemma-2"})
    clear_memory()
    monkeypatch.setenv("SEMANTICA_EMBEDDING_CACHE", str(path))

    assert cache_stamp() is not None
    found = lookup_vectors(["https://ex.org/b", "https://ex.org/missing"])
    assert list(found) == ["https://ex.org/b"]
    assert found["https://ex.org/b"] == pytest.approx([0.0, 1.0, 0.0])

    graph = ContextGraph(advanced_analytics=False)
    graph.add_node("https://ex.org/a", "owl:Class", content="A")
    graph.add_node(
        "https://ex.org/owned",
        "owl:Class",
        content="owned",
        semantic_embedding=[9.0, 9.0, 9.0],
    )
    graph.add_node("https://ex.org/b", "owl:Class", content="B")
    session = GraphSession(graph)
    embeddings = session.get_cached_embeddings()

    assert embeddings["https://ex.org/a"] == pytest.approx([1.0, 0.0, 0.0])
    assert embeddings["https://ex.org/b"] == pytest.approx([0.0, 1.0, 0.0])
    assert embeddings["https://ex.org/owned"] == pytest.approx([9.0, 9.0, 9.0])
    assert "https://ex.org/missing" not in embeddings

    # A replacement archive is visible without a graph edit.
    write_cache(
        path,
        ["https://ex.org/a"],
        np.array([[0.0, 0.0, 1.0]], dtype=np.float32),
    )
    clear_memory()
    refreshed = session.get_cached_embeddings()
    assert refreshed["https://ex.org/a"] == pytest.approx([0.0, 0.0, 1.0])
    assert "https://ex.org/b" not in refreshed


def test_missing_archive_leaves_property_vectors_only(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMANTICA_EMBEDDING_CACHE", str(tmp_path / "absent.npz"))
    graph = ContextGraph(advanced_analytics=False)
    graph.add_node("https://ex.org/plain", "owl:Class", content="plain")
    session = GraphSession(graph)
    assert session.get_cached_embeddings() == {}
