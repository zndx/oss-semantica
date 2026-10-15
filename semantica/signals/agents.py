"""``zndx.agent.v1.Agents`` on the Semantica lattice port.

Peers call this service. The servicer drives a Grok ACP session and
writes decisions through the Semantica HTTP API. It does not live inside
the HTTP server and it does not own a second graph.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from semantica import __version__

AGENT_NAME = "grok-build"


def agent_id() -> str:
    return f"semantica/{AGENT_NAME}@{__version__}"


@dataclass
class Caller:
    project: str = ""
    agent: str = ""
    run_id: str = ""
    tx_id: str = ""
    reason: str = ""

    def metadata(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "caller_project": self.project,
                "caller_agent": self.agent,
                "run_id": self.run_id,
                "tx_id": self.tx_id,
            }.items()
            if value
        }


def caller_from(request, context) -> Caller:
    metadata = {}
    for item in context.invocation_metadata() or []:
        metadata[str(item[0]).lower()] = item[1]
    extra: dict[str, Any] = {}
    raw = getattr(request, "kwargs_json", "") or ""
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            extra = parsed
    project = str(extra.get("caller_project") or metadata.get("x-signals-project") or "")
    agent = str(extra.get("caller_agent") or metadata.get("x-signals-agent") or "")
    return Caller(
        project=project,
        agent=agent,
        run_id=str(getattr(request, "run_id", "") or "") or str(uuid.uuid4()),
        tx_id=str(getattr(request, "tx_id", "") or ""),
        reason=str(getattr(request, "reason", "") or ""),
    )


class ScriptedDriver:
    """Test double. Records one decision and finishes. Does not spawn Grok."""

    def run(self, request, caller: Caller, api, cancel: threading.Event) -> Iterator[dict]:
        if cancel.is_set():
            yield {"kind": "done", "state": "cancelled", "text": "cancelled"}
            return
        recorded = api.record_decision(
            {
                "category": "agent_run",
                "scenario": (request.instruction or "")[:5000] or "empty instruction",
                "reasoning": "Scripted driver recorded the request without spawning Grok.",
                "outcome": "recorded",
                "confidence": 1.0,
                "decision_maker": f"acp/{AGENT_NAME}",
                **caller.metadata(),
            }
        )
        decision_id = recorded.get("decision_id", "")
        yield {"kind": "tool", "name": "record_decision", "arguments": {"decision_id": decision_id}}
        yield {"kind": "message", "text": f"Recorded {decision_id}."}
        yield {"kind": "done", "state": "completed", "text": f"Recorded {decision_id}.", "decision_id": decision_id}


@dataclass
class RunState:
    run_id: str
    agent_id: str
    state: str = "running"
    started_unix_ms: int = 0
    ended_unix_ms: int = 0
    error: str = ""
    result_text: str = ""
    tx_id: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)


class AgentsService:
    """In-memory run table plus the ACP driver."""

    def __init__(self, api=None, driver=None, peers: list[str] | None = None, status_fn=None) -> None:
        self.api = api
        self.driver = driver if driver is not None else ScriptedDriver()
        self.peers = peers
        self.status_fn = status_fn
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()

    def offer(self):
        from signals_engine.wire.zndx.agent.v1 import agent_pb2

        return agent_pb2.AgentOffer(
            agent_id=agent_id(),
            project="semantica",
            name=AGENT_NAME,
            version=__version__,
            transport=agent_pb2.AGENT_TRANSPORT_ACP,
            model_capabilities=["thinking"],
            mcp=[agent_pb2.MCP_TRANSPORT_HTTP],
            workspace=False,
            behaviours=[
                "ontology-search",
                "ontology-ingest",
                "forward-chain",
                "decisions",
                "lattice-complete",
            ],
            billing=agent_pb2.BILLING_SUBSCRIPTION,
            note=(
                "Subscription Grok over ACP. Reads and writes the Semantica "
                "ontology and decision graph through the HTTP API. May call a "
                "lattice peer that advertises thinking. Does not start a local model."
            ),
        )

    def list_agents(self):
        from signals_engine.wire.zndx.agent.v1 import agent_pb2

        return agent_pb2.ListAgentsResponse(project="semantica", agents=[self.offer()])

    def _api(self):
        if self.api is not None:
            return self.api
        from semantica.signals.api import SemanticaApi

        return SemanticaApi()

    def _resolve_thinking(self) -> str | None:
        from semantica.signals.lattice import find_capability

        return find_capability("thinking", peers=self.peers, status_fn=self.status_fn)

    def run(self, request, context) -> Iterator:
        from signals_engine.wire.zndx.agent.v1 import agent_pb2

        name = (request.agent or AGENT_NAME).strip()
        if name not in {AGENT_NAME, agent_id()}:
            context.abort(
                __import__("grpc").StatusCode.INVALID_ARGUMENT,
                f"this engine hosts {AGENT_NAME}",
            )
        capability = (request.model_capability or "").strip()
        thinking_peer = ""
        if capability and capability != "thinking":
            context.abort(
                __import__("grpc").StatusCode.FAILED_PRECONDITION,
                f"model capability {capability} is not offered",
            )
        if capability == "thinking":
            thinking_peer = self._resolve_thinking() or ""
            if not thinking_peer:
                context.abort(
                    __import__("grpc").StatusCode.FAILED_PRECONDITION,
                    "thinking is not advertised by any configured lattice peer",
                )
        caller = caller_from(request, context)
        state = RunState(
            run_id=caller.run_id,
            agent_id=agent_id(),
            started_unix_ms=int(time.time() * 1000),
            tx_id=caller.tx_id,
        )
        with self._lock:
            self._runs[caller.run_id] = state
        seq = 0

        def emit(message) -> object:
            nonlocal seq
            seq += 1
            message.run_id = caller.run_id
            message.seq = seq
            message.at_unix_ms = int(time.time() * 1000)
            return message

        yield emit(agent_pb2.AgentEvent(
            accepted=agent_pb2.RunAccepted(
                run_id=caller.run_id,
                agent_id=agent_id(),
                model="grok-build" if not thinking_peer else "grok-build+thinking",
                model_peer=thinking_peer or "semantica",
            )
        ))
        api = self._api()
        final = "completed"
        text = ""
        error = ""
        try:
            for item in self.driver.run(request, caller, api, state.cancel):
                if state.cancel.is_set() and item.get("kind") != "done":
                    final = "cancelled"
                    break
                kind = item.get("kind")
                if kind == "message":
                    text = str(item.get("text") or "")
                    yield emit(agent_pb2.AgentEvent(
                        message=agent_pb2.Message(turn=1, role="assistant", text=text)
                    ))
                elif kind == "tool":
                    yield emit(agent_pb2.AgentEvent(
                        tool_call=agent_pb2.ToolCall(
                            turn=1,
                            call_id=str(item.get("call_id") or seq),
                            name=str(item.get("name") or ""),
                            arguments_json=json.dumps(item.get("arguments") or {}),
                            server="semantica",
                        )
                    ))
                elif kind == "done":
                    final = str(item.get("state") or "completed")
                    text = str(item.get("text") or text)
                    error = str(item.get("error") or "")
        except Exception as exc:
            final = "failed"
            error = str(exc)
        state.ended_unix_ms = int(time.time() * 1000)
        state.result_text = text
        state.error = error
        state.state = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(final, "failed")
        done_state = {
            "completed": agent_pb2.RUN_COMPLETED,
            "failed": agent_pb2.RUN_FAILED,
            "cancelled": agent_pb2.RUN_CANCELLED,
        }.get(final, agent_pb2.RUN_FAILED)
        yield emit(agent_pb2.AgentEvent(
            done=agent_pb2.RunDone(
                state=done_state,
                stop_reason="end_turn" if final == "completed" else final,
                result_text=text,
                error=error,
            )
        ))

    def cancel(self, run_id: str, reason: str = "") -> bool:
        with self._lock:
            state = self._runs.get(run_id)
        if state is None:
            return False
        state.cancel.set()
        state.state = "cancelled"
        state.error = reason
        return True

    def get(self, run_id: str) -> RunState | None:
        with self._lock:
            return self._runs.get(run_id)


def build_native_service(service: AgentsService | None = None):
    """Register Agents beside Engine and OIP on the lattice port."""
    import grpc
    from signals_engine.types import NativeService
    from signals_engine.wire.zndx.agent.v1 import agent_pb2, agent_pb2_grpc

    host = service or AgentsService(driver=None)
    # The default process driver is the subscription Grok host. Tests pass
    # AgentsService(driver=ScriptedDriver()) and never reach this import.
    if service is None:
        from semantica.signals.acp_driver import GrokSubscriptionDriver

        host.driver = GrokSubscriptionDriver()

    class Servicer(agent_pb2_grpc.AgentsServicer):
        def ListAgents(self, request, context):
            return host.list_agents()

        def Run(self, request, context):
            return host.run(request, context)

        def Cancel(self, request, context):
            accepted = host.cancel(request.run_id, request.reason)
            state = host.get(request.run_id)
            code = agent_pb2.RUN_CANCELLED if accepted else agent_pb2.RUN_STATE_UNSPECIFIED
            if state and state.state == "completed":
                code = agent_pb2.RUN_COMPLETED
            return agent_pb2.CancelResponse(accepted=accepted, state=code)

        def GetRun(self, request, context):
            state = host.get(request.run_id)
            if state is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "run not found")
            mapping = {
                "running": agent_pb2.RUN_RUNNING,
                "completed": agent_pb2.RUN_COMPLETED,
                "failed": agent_pb2.RUN_FAILED,
                "cancelled": agent_pb2.RUN_CANCELLED,
            }
            return agent_pb2.RunRecord(
                run_id=state.run_id,
                agent_id=state.agent_id,
                state=mapping.get(state.state, agent_pb2.RUN_STATE_UNSPECIFIED),
                started_unix_ms=state.started_unix_ms,
                ended_unix_ms=state.ended_unix_ms,
                tx_id=state.tx_id,
                error=state.error,
            )

    full_name = agent_pb2.DESCRIPTOR.services_by_name["Agents"].full_name
    return NativeService(
        add_to_server=agent_pb2_grpc.add_AgentsServicer_to_server,
        servicer=Servicer(),
        full_name=full_name,
    )
