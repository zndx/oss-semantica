"""Lattice host for the pinned ``signals-engine`` release.

Semantica joins the peer lattice by importing that package. ``LatticeEngine``
registers ``zndx.engine.v1.Engine`` and KServe OIP on one port. Product
servicers, when this project has them, are ``NativeService`` values on that
same port. Protocol bytes stay in the wheel (``PROTOCOL_SHA``, ``proto_root``,
``signals_engine.wire``).
"""

from semantica.signals.host import (
    EXPECTED_PROTOCOL_SHA,
    SIGNALS_ENGINE_REQUIREMENT,
    SIGNALS_ENGINE_TAG,
    build_engine,
    main,
    peer_identity,
    serve_lattice,
)

__all__ = [
    "EXPECTED_PROTOCOL_SHA",
    "SIGNALS_ENGINE_REQUIREMENT",
    "SIGNALS_ENGINE_TAG",
    "build_engine",
    "main",
    "peer_identity",
    "serve_lattice",
]
