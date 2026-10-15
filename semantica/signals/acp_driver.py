"""Subscription Grok ACP driver.

The engine spawns ``grok agent stdio`` and hands it a stdio MCP server
that calls the Semantica HTTP API. Filesystem and terminal requests are
denied. ``XAI_*`` is stripped so the CLI stays on the subscription
login in ``~/.grok/auth.json`` and does not switch to per-token billing.

``thinking`` is not a local process. When a run asks for it, the MCP
child receives the peer address that already advertises the capability.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterator

from semantica.signals.agents import AGENT_NAME, Caller


def grok_bin() -> str | None:
    explicit = (os.environ.get("SEMANTICA_GROK_BIN") or "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    return shutil.which("grok")


def subscription_ready() -> str | None:
    """None when Grok can start. Otherwise a sentence the run can return."""
    if grok_bin() is None:
        return "grok is not on PATH. Set SEMANTICA_GROK_BIN or install the Grok CLI."
    if not (Path.home() / ".grok/auth.json").exists():
        return "grok has no subscription login. Run `grok login` before starting a Semantica agent run."
    return None


class GrokSubscriptionDriver:
    def run(self, request, caller: Caller, api, cancel) -> Iterator[dict]:
        problem = subscription_ready()
        if problem:
            yield {"kind": "done", "state": "failed", "error": problem, "text": problem}
            return
        try:
            from acp import Client, PROTOCOL_VERSION, spawn_agent_process, text_block
        except ImportError:
            text = (
                "agent-client-protocol is not installed. "
                "Install the signals extra."
            )
            yield {"kind": "done", "state": "failed", "error": text, "text": text}
            return
        opening = api.record_decision(
            {
                "category": "agent_run",
                "scenario": (getattr(request, "instruction", "") or "")[:5000] or "empty instruction",
                "reasoning": "The lattice engine accepted an Agents/Run and is starting subscription Grok.",
                "outcome": "started",
                "confidence": 1.0,
                "decision_maker": f"acp/{AGENT_NAME}",
                **caller.metadata(),
            }
        )
        decision_id = str((opening or {}).get("decision_id") or "")
        yield {"kind": "tool", "name": "record_decision", "arguments": {"decision_id": decision_id}}

        import asyncio

        events = asyncio.run(self._session(
            request, caller, cancel, Client, PROTOCOL_VERSION, spawn_agent_process, text_block, decision_id
        ))
        yield from events

    async def _session(self, request, caller, cancel, Client, protocol_version, spawn, text_block, decision_id: str):
        from acp.schema import EnvVariable, Implementation, McpServerStdio

        events: list[dict] = []

        class Deny(Client):
            async def read_text_file(self, path, session_id, **kwargs):
                return None

            async def write_text_file(self, path, content, session_id, **kwargs):
                return False

            async def create_terminal(self, **kwargs):
                return None

            async def session_update(self, session_id, update, **kwargs):
                content = getattr(update, "content", None)
                text = getattr(content, "text", None)
                if text:
                    events.append({"kind": "message", "text": text})

        env = {key: value for key, value in os.environ.items() if not key.startswith("XAI_")}
        thinking = (os.environ.get("SEMANTICA_THINKING_PEER") or "").strip()
        capability = (getattr(request, "model_capability", "") or "").strip()
        if capability == "thinking" and thinking:
            env["SEMANTICA_THINKING_PEER"] = thinking
        python = os.environ.get("SEMANTICA_TOOL_PYTHON") or os.path.realpath(sys.executable)
        command = grok_bin()
        client = Deny()
        manager = spawn(
            client,
            command,
            "agent",
            "--always-approve",
            "--model",
            "grok-build",
            "stdio",
            env=env,
            cwd=os.getcwd(),
        )
        connection = None
        try:
            connection, _process = await manager.__aenter__()
            await connection.initialize(
                protocol_version=protocol_version,
                client_info=Implementation(name="semantica-engine", version="0.7.0"),
            )
            session = await connection.new_session(
                cwd=os.getcwd(),
                mcp_servers=[
                    McpServerStdio(
                        name="semantica",
                        command=python,
                        args=["-m", "semantica.signals.tool_server"],
                        env=_mcp_env(env),
                    )
                ],
            )
            prompt = _prompt(
                request,
                caller,
                thinking if capability == "thinking" else "",
                decision_id,
            )
            await connection.prompt(prompt=[text_block(prompt)], session_id=session.session_id)
        finally:
            if connection is not None:
                await manager.__aexit__(None, None, None)
        if cancel.is_set():
            events.append({"kind": "done", "state": "cancelled", "text": "cancelled"})
            return events
        if not events:
            events.append({"kind": "message", "text": "Grok finished without a text update."})
        events.append({"kind": "done", "state": "completed", "text": events[-1].get("text", "")})
        return events


def _mcp_env(env: dict[str, str]):
    from acp.schema import EnvVariable

    names = (
        "SEMANTICA_HTTP_URL",
        "SEMANTICA_API_KEY",
        "SEMANTICA_THINKING_PEER",
        "SEMANTICA_LATTICE_PEERS",
        "PATH",
        "HOME",
    )
    return [EnvVariable(name=name, value=env[name]) for name in names if env.get(name)]


def _prompt(request, caller: Caller, thinking_peer: str, decision_id: str) -> str:
    lines = [
        "You are the Semantica lattice agent, a full Grok subscription session.",
        "Use the semantica MCP tools for the ontology and for decisions.",
        "Before a consequential judgment, call find_precedents.",
        "Record each judgment with record_decision and link it with link_decisions when one decision causes the next.",
        "Cite node ids returned by search_nodes or get_node. Do not invent entities.",
        "Forward chaining is the reason tool. Ingest is load_ontology.",
        "Do not edit files and do not open a shell.",
        f"Caller project: {caller.project or 'unknown'}.",
        f"Run id: {caller.run_id}. Transaction id: {caller.tx_id or 'none'}.",
        f"Opening decision id: {decision_id or 'none'}. Link later judgments to it.",
    ]
    if thinking_peer:
        lines.append(
            f"The lattice peer {thinking_peer} advertises thinking. "
            "Use lattice_complete with capability thinking when a local model trace would help."
        )
    else:
        lines.append("No lattice peer is currently advertised for thinking. Do not assume a local model.")
    if caller.reason:
        lines.append(f"Why this run exists: {caller.reason}")
    lines.append("")
    lines.append(request.instruction or "")
    if getattr(request, "system_prompt", ""):
        lines.append(request.system_prompt)
    return "\n".join(lines)
