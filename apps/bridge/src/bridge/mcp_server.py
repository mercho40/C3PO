"""C3PO bridge — the MCP server entrypoint.

Exposes the robot's skills as MCP tools so Claude Code (or any MCP client, and
`apps/back`'s internal agent) can drive the bridge directly.

Where things live:

- `bridge.config` — environment, read once at import (loads the bridge's `.env`)
- `bridge.server` — the FastMCP instance, and DDS initialisation outside stub
- `bridge.tools.*` — the MCP tools, one module per domain (the skill catalogue)
- `bridge.routes.*` — plain-HTTP routes on the same port (telemetry, voice, camera)
- this module — boot-time background workers and the transport

Modes:
- `SIM_MODE=stub`: tools log and return fake data — for wiring validation.
- `SIM_MODE=isaac`: DDS is initialised at import; `get_state` reads live
  `rt/lowstate` + `rt/sim_state`; `walk_to` drives Isaac Sim via
  `rt/run_command/cmd` with a body-frame velocity loop.
- `SIM_MODE=real`: the bridge runs onboard the Jetson (docs/ARCHITECTURE.md §2).

Long-running tools use the shared task lifecycle in
`bridge.skills.task_runtime`: each invocation creates a `Task` row, the
skill checks `task.cancel_event` between iterations, and progress is
emitted through MCP's `ctx.report_progress` when a `Context` is passed.
The companion `cancel_task` and `list_active_tasks` tools provide
visibility / control across the registry.

Run:
    uv run python -m bridge.mcp_server

Registered in `.mcp.json` as `c3po-sim` — a spawned child process speaking
stdio, the default transport. The `c3po-bridge` entry in `.mcp.json` is a
different thing: the daemon running onboard the Jetson, reached over HTTP on
port 8001 — i.e. the REAL robot.
"""

from __future__ import annotations

import asyncio

import structlog

# Config first: it loads the bridge's `.env`, which `bridge.watchdog` and the
# skill modules read at import time.
from bridge.config import (
    BRIDGE_CORS_ORIGINS,
    BRIDGE_HOST,
    BRIDGE_PORT,
    BRIDGE_TRANSPORT,
    SIM_MODE,
)

from bridge import routes, tools, watchdog  # noqa: E402, F401 - routes/tools register on import
from bridge.server import mcp  # noqa: E402
from bridge.watchdog import get_watchdog  # noqa: E402

log = structlog.get_logger(__name__)


async def _run_streamable_http() -> None:
    """Run FastMCP with narrowly scoped browser origins for direct camera access."""
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware
    from starlette.types import ASGIApp

    app: ASGIApp = mcp.streamable_http_app()
    if BRIDGE_CORS_ORIGINS:
        app = CORSMiddleware(
            app,
            allow_origins=list(BRIDGE_CORS_ORIGINS),
            allow_methods=["GET"],
            allow_headers=["*"],
        )
    config = uvicorn.Config(
        app,
        host=BRIDGE_HOST,
        port=BRIDGE_PORT,
        log_level=mcp.settings.log_level.lower(),
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    """Run the MCP server.

    Default transport is stdio (Claude Code / Claude Desktop). Set
    ``BRIDGE_TRANSPORT=http`` to serve FastMCP's streamable-http transport at
    ``http://{BRIDGE_HOST}:{BRIDGE_PORT}/mcp`` — how ``apps/back`` connects.
    """
    # Operator-link watchdog (docs/ARCHITECTURE.md §7). Real hardware only, and OFF unless
    # `LINK_WATCHDOG=on` — read `bridge/watchdog.py` before arming it.
    #
    # Short version: today's tool calls block the transport, so "operator
    # silent while a task runs" is the normal state during any long skill, not
    # a dead link. Arming it now would cancel every walk_to mid-stride. It
    # becomes correct once long skills return a task_id immediately and the
    # operator's progress polls become a real liveness signal.
    if SIM_MODE == "real" and watchdog.ENABLED:
        get_watchdog().start()
    elif SIM_MODE == "real":
        log.info("watchdog.disabled", reason="LINK_WATCHDOG not set")

    # JOIN DOMAIN 42. Nothing did this before, and the omission was invisible
    # because every symptom looked like a problem somewhere else:
    #
    #   * /telemetry/costmap.png returned 503 "no costmap received" while Nav2
    #     was publishing costmaps a process away — indistinguishable from Nav2
    #     not running, which is exactly what the hint text suggests.
    #   * `ros2 topic info /c3po/cmd_vel --verbose` reported Subscription
    #     count: 0, so the cmd_vel gate had nothing to refuse. A gate that
    #     never receives cannot be shown to be closed; it is just absent.
    #   * describe_surroundings kept reporting perception offline, which reads
    #     as an honest "not deployed yet" rather than as an unwired link.
    #
    # PerceptionLink was fully written and tested throughout — it was simply
    # never started, so domain 42 had no participant on this side at all.
    #
    # STARTING IS NOT ARMING, and that separation is what makes this safe to do
    # at boot. start() creates the participant and the readers and nothing
    # else; the gate stays default-closed with disabled_reason "never armed".
    # _tick() returns before touching _forward while the gate is shut, so
    # _default_forward — the one path where a Twist becomes SET_VELOCITY —
    # remains unreachable until something arms the gate deliberately. Nothing
    # can today: `arm_navigation` is Stage 8 and does not exist yet.
    #
    # Failure here must not take the bridge down. A robot whose MCP server
    # refuses to boot has no stop_everything, which is a far worse state than
    # one that cannot see its costmap — and this is the first thing that runs
    # after a reboot, where DDS is exactly what is flaky (see the eth0 boot
    # race in docs/OPERATIONS.md).
    if SIM_MODE in ("real", "isaac"):
        # Local import for the same reason the routes use one: module scope must
        # stay free of CycloneDDS so the bridge imports on a laptop.
        from bridge.sdk.perception_link import get_link

        try:
            get_link().start()
            log.info("perception.link.started", domain=42, gate="closed")
        except Exception:
            log.exception("perception.link.start_failed")

        # START LISTENING AT BOOT, not on the first `listen` call. Otherwise the
        # first thing anyone says is the one thing the robot cannot hear: the
        # buffer would only begin filling once the agent thought to ask, and
        # people speak before they are asked to.
        #
        # This is cheap while nobody is talking — the mic is push-to-talk, so a
        # closed feed delivers no packets and the thread sits in a socket
        # timeout. There is no audio to decode until somebody holds L1+L2.
        #
        # And that button is the privacy boundary, which is worth being explicit
        # about: the robot transcribes only while a person is deliberately
        # holding a control to talk to it. Transcripts live in a bounded
        # in-memory buffer and are never written to disk.
        try:
            from bridge.skills.listen import available as stt_available
            from bridge.skills.listen import get_mic_listener

            ok, why = stt_available()
            if ok:
                get_mic_listener().start()
                log.info("mic_listener.started")
            else:
                # Not an error: the bridge runs fine without ears, and `listen`
                # reports the reason with the fix attached when asked.
                log.info("mic_listener.disabled", reason=why)
        except Exception:
            log.exception("mic_listener.start_failed")

        # `rt/audio_msg` — the completion signal for our own speech.
        #
        # This module was written, measured and tested, and then called by
        # nothing: `get_audio_msg_link()` had zero call sites in the whole
        # source tree, so the one topic that can say "the robot has stopped
        # talking" was never subscribed. Found by
        # `tests/test_singletons_are_started.py`, which exists because this is
        # the fifth time a finished component here was wired to nothing.
        #
        # Cheap and passive: one DDS subscriber on a topic the firmware already
        # publishes. It claims no device and commands nothing.
        try:
            from bridge.sdk.audio_msg import get_audio_msg_link

            get_audio_msg_link().start()
        except Exception:
            log.exception("audio_msg.start_failed")

        # THE HEAD CAMERA, at boot, and only on real hardware.
        #
        # Started here rather than on the first `/camera/*` request for the same
        # reason the mic is: the operator who opens the console wants a picture
        # now, and a feed that only begins filling when somebody asks is a feed
        # that is always a beat behind. It is one 175 KB JPEG every 100 ms into
        # a single buffer, and it costs nothing while nobody is watching.
        #
        # This does NOT touch the camera device. It reads the vendor's videohub
        # RPC, which works precisely because `videohub_pc4` still owns
        # /dev/video4 — see bridge/sdk/videohub.py. If somebody has taken the
        # device for the detector, this stays dark and says so; that is an
        # ordinary state, not a failure, and it must not be loud.
        #
        # Real only, not isaac: `videohub_pc4` is a service on the physical
        # robot's Jetson. In the sim the cameras are teleimager WebRTC servers
        # and a different page speaks to them.
        if SIM_MODE == "real":
            try:
                from bridge.sdk.videohub import get_camera

                get_camera().start()
            except Exception:
                log.exception("videohub.camera.start_failed")

    if BRIDGE_TRANSPORT in ("http", "streamable-http"):
        log.info(
            "c3po-bridge.start",
            sim_mode=SIM_MODE,
            transport="streamable-http",
            host=BRIDGE_HOST,
            port=BRIDGE_PORT,
        )
        asyncio.run(_run_streamable_http())
    else:
        log.info("c3po-bridge.start", sim_mode=SIM_MODE, transport="stdio")
        mcp.run()  # default transport is stdio


if __name__ == "__main__":
    main()
