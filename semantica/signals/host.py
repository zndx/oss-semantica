"""Build a Semantica peer on the shared lattice engine.

The process does not implement ``Engine`` or OIP. It names itself, optionally
points at the HTTP surface, and hands any product servicers to
``LatticeEngine`` to register beside those two faces.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from semantica import __version__
from semantica.utils.exceptions import ProcessingError

log = logging.getLogger("semantica.signals")

# v0.2.0. A later tag replaces this pin in pyproject.toml; the protocol
# commit below is the stamp of that tag.
SIGNALS_ENGINE_TAG = "v0.2.0"
SIGNALS_ENGINE_REQUIREMENT = (
    "signals-engine @ git+https://github.com/weathership/signals-engine.git@v0.2.0"
)
EXPECTED_PROTOCOL_SHA = "fa0c09e5a9c4fd551810efbbfc5b80685d05d1fd"

_INSTALL_HINT = (
    "signals-engine is not installed. On Python 3.11 or newer, install it with: "
    "pip install 'semantica[signals]'"
)


def load_signals_engine():
    """Import the pinned lattice package, or raise with the extra name."""
    try:
        import signals_engine
    except ImportError as exc:
        raise ProcessingError(_INSTALL_HINT) from exc
    return signals_engine


def peer_identity(
    *,
    port: int | None = None,
    bind_host: str | None = None,
    advertise_host: str | None = None,
    primary_ui: str | None = None,
    health_url: str | None = None,
    repo_root: Path | None = None,
):
    """Identity of this process. The project name is always ``semantica``."""
    signals_engine = load_signals_engine()
    if port is None:
        raw = (os.environ.get("SIGNALS_ENGINE_PORT") or "0").strip() or "0"
        port = int(raw)
    if bind_host is None:
        bind_host = (os.environ.get("SIGNALS_ENGINE_BIND") or "127.0.0.1").strip()
        bind_host = bind_host or "127.0.0.1"
    if advertise_host is None:
        # An explicit loopback advertise host is how a local process dials
        # itself. Any other bind leaves the host empty so the library resolver
        # can publish a non-loopback name.
        if bind_host in {"127.0.0.1", "localhost", "::1"}:
            advertise_host = "127.0.0.1"
        else:
            advertise_host = ""
    if primary_ui is None:
        primary_ui = (os.environ.get("SEMANTICA_PRIMARY_UI") or "").strip()
    if health_url is None:
        health_url = (os.environ.get("SEMANTICA_HEALTH_URL") or "").strip()
    return signals_engine.PeerIdentity(
        project="semantica",
        port=port,
        bind_host=bind_host,
        advertise_host=advertise_host,
        primary_ui=primary_ui,
        health_url=health_url,
        repo_root=repo_root,
        version=__version__,
    )


def build_engine(
    *,
    identity=None,
    hooks=None,
    native: tuple = (),
    directory: bool = False,
    announce_to_directory: bool = False,
    contract_path: Path | None = None,
    directory_targets: tuple[str, ...] = (),
    lineage_url: str | None = None,
):
    """Construct the shared engine. Does not bind a port."""
    signals_engine = load_signals_engine()
    if identity is None:
        identity = peer_identity()
    if hooks is None:
        hooks = signals_engine.EngineHooks(native=tuple(native))
    elif native:
        hooks.native = tuple(hooks.native) + tuple(native)
    return signals_engine.LatticeEngine(
        identity,
        hooks,
        directory=directory,
        announce_to_directory=announce_to_directory,
        contract_path=contract_path,
        directory_targets=directory_targets,
        lineage_url=lineage_url,
    )


def serve_lattice(*, block: bool = True, **kwargs):
    """Bind Engine, OIP, reflection, and any native servicers on one port."""
    engine = build_engine(**kwargs)
    engine.serve(block=block)
    return engine


def main() -> None:
    """Join the lattice. The HTTP API stays a separate process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signals_engine = load_signals_engine()
    directory = (os.environ.get("SIGNALS_ENGINE_DIRECTORY") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    announce_raw = (os.environ.get("SIGNALS_ENGINE_ANNOUNCE") or "1").strip().lower()
    announce = announce_raw not in {"0", "false", "no"}
    log.info(
        "semantica lattice pin %s protocol %s",
        SIGNALS_ENGINE_TAG,
        signals_engine.PROTOCOL_SHA,
    )
    from semantica.signals.agents import build_native_service

    serve_lattice(
        directory=directory,
        announce_to_directory=announce,
        native=(build_native_service(),),
        block=True,
    )
