"""The lattice Agents service records the caller's protocol ids."""

import pytest

pytest.importorskip("signals_engine")

from signals_engine.wire.zndx.agent.v1 import agent_pb2

from semantica.signals.agents import AgentsService, ScriptedDriver


class _Api:
    def __init__(self):
        self.records = []

    def record_decision(self, payload):
        self.records.append(payload)
        return {"decision_id": "dec-recorded"}


class _Context:
    def invocation_metadata(self):
        return [("x-signals-project", "hermes"), ("x-signals-agent", "overwatch")]

    def abort(self, code, details):
        raise RuntimeError(details)


def test_list_agents_advertises_grok_and_the_ontology_behaviours():
    service = AgentsService(api=_Api(), driver=ScriptedDriver())
    response = service.list_agents()
    assert response.project == "semantica"
    offer = response.agents[0]
    assert offer.name == "grok-build"
    assert offer.transport == agent_pb2.AGENT_TRANSPORT_ACP
    assert offer.billing == agent_pb2.BILLING_SUBSCRIPTION
    assert "forward-chain" in offer.behaviours
    assert "ontology-ingest" in offer.behaviours
    assert "decisions" in offer.behaviours


def test_run_records_the_caller_from_grpc_metadata():
    api = _Api()
    service = AgentsService(api=api, driver=ScriptedDriver())
    request = agent_pb2.RunRequest(
        agent="grok-build",
        instruction="Explain why LIMS is broader than specimen lifecycle.",
        run_id="run-77",
        tx_id="tx-77",
    )
    events = list(service.run(request, _Context()))
    kinds = [event.WhichOneof("event") for event in events]
    assert kinds[0] == "accepted"
    assert kinds[-1] == "done"
    assert events[-1].done.state == agent_pb2.RUN_COMPLETED
    assert api.records[0]["caller_project"] == "hermes"
    assert api.records[0]["caller_agent"] == "overwatch"
    assert api.records[0]["run_id"] == "run-77"
    assert api.records[0]["tx_id"] == "tx-77"


def test_thinking_is_refused_when_no_peer_advertises_it():
    service = AgentsService(api=_Api(), driver=ScriptedDriver(), peers=[], status_fn=lambda peer: None)
    request = agent_pb2.RunRequest(agent="grok-build", model_capability="thinking", instruction="think")
    with pytest.raises(RuntimeError, match="not advertised"):
        list(service.run(request, _Context()))
