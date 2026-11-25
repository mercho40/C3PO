"""C3PO bridge — the MCP server's entry point.

Exposes the robot's skills as MCP tools so Claude Code (or any MCP client, and
apps/back) can drive the bridge directly. This module assembles the server and
runs it; the pieces live in:

- `bridge.config` — the `.env` and the bridge's settings, read once at import
- `bridge.server` — the `mcp` instance and its gate (GovernedMCP); DDS starts there
- `bridge.tools.*` — the MCP tools, one module per domain (the skill catalogue)
- `bridge.routes.*` — plain-HTTP routes on the same port (telemetry, safety,
  face, camera, voice)
- this module — what runs before serving (`main`) and the two transports, both
  inside the foreign-stop watcher and the greeter

Modes:
- `SIM_MODE=stub`: tools log and return fake data — for wiring validation.
- `SIM_MODE=isaac`: DDS is initialised at import; `get_state` reads live
  `rt/lowstate` + `rt/sim_state`; `walk_to` drives Isaac Sim via
  `rt/run_command/cmd` with a body-frame velocity loop.

Long-running tools (today: `walk_to`) use the shared task lifecycle in
`bridge.task_runtime`: each invocation creates a `Task` row, the
skill checks `task.cancel_event` between iterations, and progress is
emitted through MCP's `ctx.report_progress` when a `Context` is passed.
The companion `cancel_task` and `list_active_tasks` tools provide
visibility / control across the registry.

Run:
    uv run python -m bridge.mcp_server

Registered in `.mcp.json` as `c3po-sim` — a spawned child process speaking
stdio, the default transport. The `c3po-bridge` entry in `.mcp.json` is a
different thing: the daemon running onboard the Jetson, reached directly over
LAN HTTP on port 8001 — i.e. the REAL robot.
"""

from __future__ import annotations

import asyncio

import structlog

# Config first: bridge.config loads the bridge's `.env`, which every module below
# reads at import time, and which decides whether bridge.server starts DDS.
from bridge import config

# These must follow the config import above; ruff's E402 is silenced per import
# rather than for the file, so a genuinely misplaced import still fails.
from bridge import face_gate, face_greet, watchdog  # noqa: E402
from bridge.server import mcp  # noqa: E402
from bridge import tools  # noqa: E402, F401 - registers the tools in bridge/tools/
from bridge import routes  # noqa: E402, F401 - registers the HTTP routes in bridge/routes/
from bridge.tools.speech import speak_and_wait, speech_busy  # noqa: E402
from bridge.watchdog import get_watchdog  # noqa: E402

log = structlog.get_logger(__name__)


def _http_app():  # noqa: ANN202 - starlette types
    """The ASGI app exactly as served, so tests can check its headers.

    NO CORS MIDDLEWARE. A site-wide one made every GET readable from the
    console origin: voice events, utterance WAVs, raw mic PCM, /face/people.
    The camera routes and /face/seen add the header themselves (camera_headers),
    and the console only sends them simple GETs, so no preflight is needed.
    """
    return mcp.streamable_http_app()


async def _run_streamable_http() -> None:
    """Run FastMCP's streamable-http transport for apps/back and the camera."""
    import uvicorn

    from bridge.skills.stop_everything import watching_foreign_stops

    served = uvicorn.Config(
        _http_app(),
        host=config.BRIDGE_HOST,
        port=config.BRIDGE_PORT,
        log_level=mcp.settings.log_level.lower(),
        # MJPEG relays and SSE never end on their own: without a bound, every restart
        # waited out the unit's TimeoutStopSec=20 and was SIGKILLed past its cleanup.
        timeout_graceful_shutdown=3,
    )
    async with watching_foreign_stops(), face_greet.greeting_operator(speak_and_wait, speech_busy):
        await uvicorn.Server(served).serve()


async def _run_stdio() -> None:
    """FastMCP's stdio transport, with the same foreign-stop watcher as HTTP."""
    from bridge.skills.stop_everything import watching_foreign_stops

    async with watching_foreign_stops(), face_greet.greeting_operator(speak_and_wait, speech_busy):
        await mcp.run_stdio_async()


def _warm_face() -> None:
    """Load the face models before serving, whatever the switches say, on a robot that has
    them: a cold `import cv2` holds the GIL for seconds, which would stall a stop if a
    console switch, an enrollment or the greeter's first tick did it while serving, and
    would spend the first gated call's TIMEOUT_S."""
    face = face_gate.settings()
    if face["gate"] or (config.SIM_MODE != "stub" and face_gate.models_installed()):
        face_gate.warm()
    if face["gate"]:
        log.warning("face_gate.on", tools=sorted(face_gate.gated_tools()), max_m=face_gate.MAX_M)
    elif face_gate.GATED:
        log.warning(
            "face_gate.env_overridden",
            env=sorted(face_gate.GATED),
            settings=str(face_gate.settings_path()),
            note="settings.json says gate off and overrides C3PO_FACE_GATE: nothing is gated",
        )


def _open_hands_at_boot() -> None:
    """Open the BrainCo hands once, on real hardware: they hold their last command and a
    restarted bridge cannot know a grip (shake_hand, a pick) was left closed. Same call as
    stop_everything's, on its own thread so a slow DDS write never delays serving."""
    if config.SIM_MODE != "real":
        return
    import threading

    from bridge.teleop import hands

    try:
        driver = hands.get_driver()
        if driver.sides:
            threading.Thread(target=driver.relax, name="hands-open-at-boot", daemon=True).start()
            log.info("hands.opened_at_boot", sides=list(driver.sides), driver=driver.name)
    except Exception:
        log.exception("hands.open_at_boot_failed")


def main() -> None:
    """Run the MCP server.

    Default transport is stdio (Claude Code / Claude Desktop). Set
    ``BRIDGE_TRANSPORT=http`` to serve FastMCP's streamable-http transport at
    ``http://{BRIDGE_HOST}:{BRIDGE_PORT}/mcp`` — how ``apps/back`` connects.
    """
    if face_gate.GATED:
        face_gate.check_config(t.name for t in mcp.registered_tools())
    _warm_face()
    _open_hands_at_boot()
    # Operator-link watchdog (docs/ARCHITECTURE.md §7). Real hardware only, and OFF unless
    # `LINK_WATCHDOG=on` — read `bridge/watchdog.py` before arming it.
    #
    # Short version: today's tool calls block the transport, so "operator
    # silent while a task runs" is the normal state during any long skill, not
    # a dead link. Arming it now would cancel every walk_to mid-stride. It
    # becomes correct once long skills return a task_id immediately and the
    # operator's progress polls become a real liveness signal.
    if config.SIM_MODE == "real" and watchdog.ENABLED:
        get_watchdog().start()
    elif config.SIM_MODE == "real":
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
    if config.SIM_MODE in ("real", "isaac"):
        # Local import for the same reason the routes use one: module scope must
        # stay free of CycloneDDS so the bridge imports on a laptop.
        from bridge.sdk.perception_link import get_link

        from bridge.object_memory import get_object_memory

        try:
            # Every accepted report also feeds the object memory (Phase 4.1).
            get_link().on_report = get_object_memory().observe_report
            get_link().start()
            log.info("perception.link.started", domain=42, gate="closed")
        except Exception:
            log.exception("perception.link.start_failed")

        # Nav2 goals ride the same participant, so this must follow it. Starting
        # it sends nothing: goals only leave through navigate_to*, which refuse
        # while the gate is shut.
        try:
            from bridge.sdk.nav_link import get_nav_link

            get_nav_link().start()
        except Exception:
            log.exception("nav.link.start_failed")

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
        if config.SIM_MODE == "real":
            try:
                from bridge.sdk.videohub import get_camera

                get_camera().start()
            except Exception:
                log.exception("videohub.camera.start_failed")

    if config.BRIDGE_TRANSPORT in ("http", "streamable-http"):
        log.info(
            "c3po-bridge.start",
            sim_mode=config.SIM_MODE,
            transport="streamable-http",
            host=config.BRIDGE_HOST,
            port=config.BRIDGE_PORT,
        )
        asyncio.run(_run_streamable_http())
    else:
        log.info("c3po-bridge.start", sim_mode=config.SIM_MODE, transport="stdio")
        asyncio.run(_run_stdio())  # default transport is stdio


if __name__ == "__main__":
    main()
