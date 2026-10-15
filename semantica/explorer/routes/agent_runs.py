"""Browser-facing proxy onto the lattice Agents service.

Peers should call ``zndx.agent.v1.Agents/Run`` on the engine port.
This route exists so the Decisions workspace can start the same run
without speaking protobuf. The HTTP process does not host ACP.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/agent", tags=["Agent"])


class AgentRunRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=8000)
    caller_project: str = "semantica-explorer"
    caller_agent: str = "decisions-workspace"
    model_capability: str = ""


def engine_target() -> str:
    explicit = (os.environ.get("SEMANTICA_ENGINE_TARGET") or "").strip()
    if explicit:
        return explicit.replace("grpc://", "")
    port = (os.environ.get("SIGNALS_ENGINE_PORT") or "50751").strip()
    return f"127.0.0.1:{port}"


def _run_blocking(body: AgentRunRequest) -> dict:
    import grpc
    from signals_engine.wire.zndx.agent.v1 import agent_pb2, agent_pb2_grpc

    request = agent_pb2.RunRequest(
        agent="grok-build",
        instruction=body.instruction,
        model_capability=body.model_capability,
        kwargs_json=(
            '{"caller_project":"%s","caller_agent":"%s"}'
            % (body.caller_project.replace('"', ""), body.caller_agent.replace('"', ""))
        ),
        reason="Explorer decision run",
    )
    metadata = (
        ("x-signals-project", body.caller_project),
        ("x-signals-agent", body.caller_agent),
    )
    events = []
    with grpc.insecure_channel(engine_target()) as channel:
        stub = agent_pb2_grpc.AgentsStub(channel)
        for event in stub.Run(request, metadata=metadata, timeout=600):
            kind = event.WhichOneof("event")
            if kind == "done":
                events.append({
                    "kind": "done",
                    "state": agent_pb2.RunState.Name(event.done.state),
                    "text": event.done.result_text,
                    "error": event.done.error,
                })
            elif kind == "message":
                events.append({"kind": "message", "text": event.message.text})
            elif kind == "tool_call":
                events.append({"kind": "tool", "name": event.tool_call.name, "arguments": event.tool_call.arguments_json})
            elif kind == "accepted":
                events.append({"kind": "accepted", "run_id": event.accepted.run_id})
    return {"target": engine_target(), "events": events}


@router.post("/runs")
async def start_run(body: AgentRunRequest):
    try:
        import signals_engine  # noqa: F401
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="The signals extra is not installed, so this process cannot dial the lattice engine.",
        ) from exc
    try:
        return await asyncio.to_thread(_run_blocking, body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agents/Run failed: {exc}") from exc
