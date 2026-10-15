"""Find a model capability another lattice peer is already serving.

Semantica does not start a local thinking model. Grok may call thinking
only when some peer's Engine/Status lists that capability as healthy.
"""

from __future__ import annotations

import os
from typing import Callable


def lattice_peers() -> list[str]:
    raw = (os.environ.get("SEMANTICA_LATTICE_PEERS") or "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def find_capability(
    capability: str,
    peers: list[str] | None = None,
    status_fn: Callable | None = None,
) -> str | None:
    """Return ``host:port`` of a peer advertising ``capability``, or None."""
    names = lattice_peers() if peers is None else list(peers)
    if not names or not capability:
        return None
    if status_fn is None:
        from signals_engine.client import status as status_fn
    for peer in names:
        try:
            report = status_fn(peer)
        except Exception:
            continue
        for endpoint in getattr(report, "endpoints", []) or []:
            if getattr(endpoint, "capability", "") == capability and getattr(endpoint, "healthy", False):
                return peer
    return None
