"""The bridge's MCP server: the `mcp` instance every tool and route registers on, and its gate.

`GovernedMCP.call_tool` is the one method both transports dispatch through
(apps/back over HTTP, Claude Code over stdio), so the risk-tier gate, the face
gate and the spoken motion warning live here and nowhere else.

Importing this starts DDS outside stub mode, after `bridge.config` has loaded the
`.env`. The tools (`bridge.tools`, still partly `bridge.mcp_server`) and the HTTP
routes (`bridge.routes`) register on `mcp` when imported; `bridge.mcp_server` imports
them all and is the entry point.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools import Tool
from pydantic import BaseModel, ValidationError

# Config first: bridge.config loads the bridge's `.env`, which the modules below
# read at import time and which decides whether DDS starts at all.
from bridge import config
from bridge import face_gate, motion_announce, safety  # noqa: E402

# Initialize DDS up-front when not in stub mode. We do this at import time so
# the subscriber is alive and accumulating LowState messages before the first
# tool call lands.
if config.SIM_MODE != "stub":
    from bridge.sdk.connection import init_dds
    from bridge.sdk.state import get_sampler

    # SIM_MODE is passed rather than left to init_dds's environment default:
    # it decides whether the config asks for unicast-to-one-peer, which is a
    # simulator workaround and a topic-hiding hazard onboard.
    init_dds(
        robot_host=config.ROBOT_HOST,
        domain_id=config.DDS_DOMAIN_ID,
        interface=config.DDS_INTERFACE,
        sim_mode=config.SIM_MODE,
    )
    # Warm the subscriber singleton so messages start flowing immediately.
    get_sampler()


def _parsed_args(tool: Tool, arguments: dict[str, Any] | None) -> BaseModel:
    """The call's arguments validated as FastMCP will validate them.

    Same model and pre-parse as FuncMetadata.call_fn_with_arg_validation, so
    defaults are filled and "0.5", 0.5, 10 and 10.0 all land on what the tool
    receives. Raises pydantic's ValidationError.
    """
    md = tool.fn_metadata
    return md.arg_model.model_validate(md.pre_parse_json(arguments or {}))


def _canonical(parsed: BaseModel) -> str:
    return json.dumps(parsed.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def canonical_args(tool: Tool, arguments: dict[str, Any] | None) -> str:
    """The call's arguments as FastMCP will validate them, as one canonical string.

    A grant is bound to this string. Raises pydantic's ValidationError.
    """
    return _canonical(_parsed_args(tool, arguments))


STOPPED_DURING_WARNING = "stopped: a stop came during the spoken warning; nothing moved"

Speaker = Callable[[str], Awaitable[Any]]

#: What speaks the motion warning: `say`, waiting for playback to end, set by the module
#: that registers `say`. Injected because `say` registers on `mcp` below, so importing
#: it here would be an import cycle.
_speaker: Speaker | None = None


def set_speaker(speaker: Speaker) -> None:
    global _speaker
    _speaker = speaker


async def _announce_speak(text: str) -> Any:
    """The motion warning speaks through `say` and waits, like the greeter: PARAR cuts it."""
    if _speaker is None:
        # motion_announce treats a speaker that raises as broken and lets the motion run,
        # as it would with a firmware that never answered.
        raise RuntimeError("no speaker: nothing registered `say`")
    return await _speaker(text)


async def confirmed_step_refusal(name: str, meta: dict[str, Any] | None, args: dict) -> str | None:
    """Why one step of an already-confirmed run_sequence must not run, or None once its
    warning has been spoken.

    call_tool's gate-then-announce for a step: PARAR, the battery and the person rule
    still apply (`confirmed=True` skips only the per-step grant, which the sequence's one
    confirmation covers), then the spoken warning. No chainable tool is in
    motion_announce.SELF, so nothing is deferred and the step's own before_motion() says
    nothing again.
    """
    refusal = safety.tool_refusal(name, meta, confirmed=True)
    if refusal is not None:
        return refusal
    if motion_announce.applies(name, meta) and not await motion_announce.announce(
        name, args, _announce_speak
    ):
        return STOPPED_DURING_WARNING
    return None


class GovernedMCP(FastMCP):
    """FastMCP whose every tool call passes the risk-tier gate first.

    Here and not in each tool so a T2 tool cannot be added without its gate:
    the tier comes from the tool's own `_meta` (skill_meta.derive_tier), and
    this is the method both transports dispatch through — apps/back over HTTP
    and Claude Code over stdio alike. Direct Python calls into the tool
    functions skip it; nothing that speaks MCP can.
    """

    # FastMCP keeps its registry private (`_tool_manager`). These are the only
    # ways the rest of the bridge reaches it, so a FastMCP upgrade that moves
    # it breaks one class, and an ungated call is named as one where it is made.

    def registered_tool(self, name: str) -> Tool | None:
        """The registered tool called `name`, for its metadata and argument model."""
        return self._tool_manager.get_tool(name)

    def registered_tools(self) -> list[Tool]:
        return self._tool_manager.list_tools()

    async def call_tool_ungated(self, name: str, arguments: dict[str, Any], *, context: Any) -> Any:
        """Run a tool WITHOUT `call_tool`'s gate. Only for a caller that has run the
        gate itself: run_sequence, whose steps pass `confirmed_step_refusal` first."""
        return await self._tool_manager.call_tool(name, arguments, context=context)

    async def call_tool(self, name, arguments):  # noqa: ANN001, ANN201 - FastMCP's signature
        from mcp.server.fastmcp.exceptions import ToolError

        from bridge import estop

        tool = self.registered_tool(name)
        meta = tool.meta if tool else None
        # Validated once: the grant is bound to it and the warning reads it.
        parsed: BaseModel | None = None
        error: ValidationError | None = None
        if tool is not None:
            try:
                parsed = _parsed_args(tool, arguments)
            except ValidationError as exc:
                error = exc
        args_key = invalid = None
        if tool is not None and safety.needs_confirmation(meta):
            if parsed is not None:
                args_key = _canonical(parsed)
            else:
                invalid = f"invalid arguments for {name}: {error}"
        face = face_gate.gated(name, meta)
        refusal = safety.tool_refusal(name, meta, args_key, spend=not face)
        if invalid is not None and not estop.stop_is_standing():
            # PARAR first; else not "get a confirmation", which /safety/confirm would refuse too.
            refusal = invalid
        if refusal is None and face:
            # On top of every check above (a face never stands in for a confirmation), and
            # before the grant is spent, so a face refusal leaves it for one retry.
            refusal = await face_gate.refusal(name, meta)
            if refusal is None:
                # Again, spending the grant: a PARAR may have come during the check.
                refusal = safety.tool_refusal(name, meta, args_key)
        if refusal is not None:
            raise ToolError(refusal)
        token = None
        # Invalid arguments go unannounced: super().call_tool raises FastMCP's own error.
        if parsed is not None and motion_announce.applies(name, meta):
            args = parsed.model_dump()
            if name in motion_announce.SELF:
                # After its planning, at its first motion command: a failed plan says nothing.
                token = motion_announce.defer(name, args, _announce_speak)
            elif not await motion_announce.announce(name, args, _announce_speak):
                raise ToolError(STOPPED_DURING_WARNING)
        try:
            return await super().call_tool(name, arguments)
        finally:
            if token is not None:
                motion_announce.reset(token)


mcp = GovernedMCP(
    name="c3po-bridge",
    instructions=(
        "Tools for controlling a Unitree G1 humanoid robot (or its Isaac Sim "
        "emulation). In stub mode, tools log and return fake data. In isaac/real "
        "mode, get_state reads live DDS state and walk_to/turn actually drive "
        "the robot. Long-running tools return a task_id; use cancel_task to "
        "interrupt one task, stop_everything to halt all motion, and "
        "list_active_tasks to see what's in flight."
    ),
    host=config.BRIDGE_HOST,
    port=config.BRIDGE_PORT,
    # No idle reaper (mcp>=1.30 defaults to 30 min): apps/back fails the first call on a
    # reaped session, and that call can be PARAR. Loopback-only; max_sessions still bounds it.
    session_idle_timeout=None,
)
