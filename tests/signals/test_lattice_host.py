"""Semantica hosts the pinned signals-engine. It does not vendor the protocol."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from semantica import __version__
from semantica.signals.host import (
    EXPECTED_PROTOCOL_SHA,
    SIGNALS_ENGINE_REQUIREMENT,
    SIGNALS_ENGINE_TAG,
    load_signals_engine,
    peer_identity,
    serve_lattice,
)
from semantica.utils.exceptions import ProcessingError

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"


def test_extra_pins_the_v020_tag():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert SIGNALS_ENGINE_TAG == "v0.2.0"
    assert SIGNALS_ENGINE_REQUIREMENT in text
    assert "python_version >= '3.11'" in text
    # The lattice extra is a peer process, not part of the KG bundle.
    all_block = text.split("all = [", 1)[1].split("]", 1)[0]
    assert "signals" not in all_block


def test_tree_does_not_vendor_signals_protocol():
    assert not (ROOT / "components" / "signals-protocol").exists()
    assert not (ROOT / ".gitmodules").exists()
    vendored = [
        path
        for path in (ROOT / "semantica").rglob("*")
        if path.name
        in {"engine_pb2.py", "engine_pb2_grpc.py", "open_inference_grpc_pb2.py"}
    ]
    assert vendored == []


def test_missing_engine_names_the_extra(monkeypatch):
    real_import = __import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "signals_engine" or name.startswith("signals_engine."):
            raise ImportError("blocked for the test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded)
    with pytest.raises(ProcessingError, match=r"semantica\[signals\]"):
        load_signals_engine()


@pytest.fixture
def isolated_lattice_env(monkeypatch):
    for name in (
        "SIGNALS_ENGINE_TARGET",
        "SIGNALS_PEER_CONTRACT",
        "SIGNALS_ATLAS_OL_URL",
        "SIGNALS_ADVERTISE_HOST",
        "SIGNALS_ENGINE_PORT",
        "SIGNALS_ENGINE_BIND",
        "SEMANTICA_PRIMARY_UI",
        "SEMANTICA_HEALTH_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _signals_engine():
    return pytest.importorskip("signals_engine")


def test_installed_pin_matches_the_v020_protocol_stamp():
    signals_engine = _signals_engine()
    assert signals_engine.__version__ == "0.2.0"
    assert signals_engine.PROTOCOL_SHA == EXPECTED_PROTOCOL_SHA
    proto = signals_engine.proto_root()
    assert (proto / "zndx" / "engine" / "v1" / "engine.proto").is_file()
    assert (proto / "inference" / "open_inference_grpc.proto").is_file()
    assert not (ROOT / "semantica" / "signals" / "proto").exists()


def test_host_serves_the_shared_faces(isolated_lattice_env):
    signals_engine = _signals_engine()
    grpc = pytest.importorskip("grpc")
    from signals_engine.client import channel
    from signals_engine.wire.inference import open_inference_grpc_pb2 as oip_pb
    from signals_engine.wire.inference import open_inference_grpc_pb2_grpc as oip_grpc
    from signals_engine.wire.zndx.engine.v1 import engine_pb2, engine_pb2_grpc

    added = {}

    def add_to_server(servicer, server):
        added["servicer"] = servicer
        added["server"] = server

    sentinel = object()
    identity = peer_identity(
        port=0,
        bind_host="127.0.0.1",
        advertise_host="127.0.0.1",
        primary_ui="http://127.0.0.1:8000/",
        health_url="",
        repo_root=None,
    )
    engine = serve_lattice(
        block=False,
        identity=identity,
        announce_to_directory=False,
        native=(
            signals_engine.NativeService(
                add_to_server,
                sentinel,
                "semantica.test.Probe",
            ),
        ),
        contract_path=ROOT / "no-such-peer-contract.json",
    )
    try:
        target = f"127.0.0.1:{engine.bound_port}"
        body = signals_engine.status(target)
        assert body.project == "semantica"
        assert body.surfaces[0].kind == "primary"
        assert body.surfaces[0].url == "http://127.0.0.1:8000/"
        assert added["servicer"] is sentinel
        assert added["server"] is not None

        with channel(target) as ch:
            reflected = oip_grpc.GRPCInferenceServiceStub(ch).ServerMetadata(
                oip_pb.ServerMetadataRequest(),
                timeout=3,
            )
            assert reflected.name == "semantica"
            assert reflected.version == __version__
            assert "zndx.engine.v1.Engine" in reflected.extensions
            assert "llm_tools_v1" not in reflected.extensions
            with pytest.raises(grpc.RpcError) as err:
                engine_pb2_grpc.EngineStub(ch).Complete(
                    engine_pb2.CompleteRequest(prompt="hi"),
                    timeout=3,
                )
            assert err.value.code() == grpc.StatusCode.UNIMPLEMENTED
            with pytest.raises(signals_engine.LatticeBindError):
                serve_lattice(
                    block=False,
                    identity=peer_identity(
                        port=engine.bound_port,
                        bind_host="127.0.0.1",
                        advertise_host="127.0.0.1",
                        primary_ui="",
                        health_url="",
                        repo_root=None,
                    ),
                    announce_to_directory=False,
                    contract_path=ROOT / "no-such-peer-contract.json",
                )
    finally:
        engine.stop()


def test_module_imports_on_the_core_interpreter():
    """The host module imports without the extra. Serving asks for it."""
    assert sys.version_info >= (3, 10)
    import semantica.signals as signals

    assert signals.SIGNALS_ENGINE_TAG == "v0.2.0"
