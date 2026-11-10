"""stop_everything: halt all motion and cancel any in-flight tasks.

Safety-critical, and fast in the common case (<1s). Two parts:
  1. Signal cancellation to every running task in the TaskRegistry. The
     skills observe `cancel_event` between iterations and stop motion
     themselves before returning.
  2. Independently send a zero-velocity burst for ~0.4 s via
     `_locomotion.stop_motion_sync`, in case no skill is currently looping
     (e.g. the policy is still moving from the last command) or in case
     the registry is out of sync with what's actually publishing. This
     goes out `rt/run_command/cmd` in sim, or as a real `SET_VELOCITY` RPC
     burst on real hardware (`_locomotion.send_velocity` dispatches on
     `SIM_MODE` — see that module).

**It stops; it damps only when the stop did not land.** On real hardware Damp
used to follow every burst. That made sense on 2026-08-07, when the burst went
to a topic the real G1 ignores and Damp was the only thing that stopped it; it
stopped making sense once the burst became a real SET_VELOCITY (b48e7c6). On
2026-10-15 PARAR damped a walking robot with the burst acknowledged — a robot
standing on the floor would have fallen. Now Damp runs only if no zero
setpoint was acknowledged (or the burst raised): the robot never heard the
stop, so going limp is the only stop left. A robot that did hear it stays in
its walk mode at zero velocity, balanced, like the watchdog leaves it.
`STOP_EVERYTHING_DAMP=always` restores the old behaviour; the `damp` tool is
still there for an operator who wants the robot limp.

**Two tiers, and `soft_stop` is the first one on its own.** The zero burst
(`_zero_burst`) is the soft stop: velocity to zero, the robot left standing.
`soft_stop` runs just that tier — close the nav gate, cancel the Nav2 goal and
the tasks, burst — for "stop walking" without a latch or a fall risk.
`stop_everything` runs the same burst and keeps everything that makes it the
e-stop: the cross-process sentinel, arms and hands let go, and Damp when the
burst was not heard.

**Cancellation happens synchronously, before anything can block.** Every
`cancel_event` is set at the top of `run()` with no awaits in between, so the
skills begin stopping themselves immediately regardless of what the RPC path
does afterwards.

**The blocking RPCs then run off the event loop, and this used to be wrong.**
This function was fully synchronous, justified by "with stdio MCP, no
concurrent tools can run anyway". That justification expired: onboard the G1
the bridge serves streamable-HTTP in one systemd-owned process
(`c3po-bridge.service` pins `BRIDGE_TRANSPORT=http`), where concurrent requests
are exactly what happens.
Meanwhile the Damp fallback below can retry 3x against a 10 s client timeout,
so a degraded link — the case the retry exists for — could wedge the whole
event loop for ~30 s. During that window a second stop press and every
`get_state` poll went unanswered: the e-stop made the robot unreachable
precisely when the operator was pressing it again. The RPCs now run on
threads of their own (`_on_own_thread`), and the retry loop carries a total
time budget on top.

When it runs, the real-hardware Damp fallback retries up to REAL_DAMP_MAX_ATTEMPTS times on
RPC failure (a dropped packet shouldn't mean a silently-failed e-stop), but
stops retrying, and waiting, once REAL_DAMP_TOTAL_BUDGET_S has elapsed. The sim-mode path
(task cancellation + zero-velocity burst) stays fast and single-shot.

Returns a list of cancelled task IDs and the duration of the stop burst.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

import structlog

from bridge import estop
from bridge.estop import signal_stop
from bridge.skills._locomotion import DEFAULT_HEIGHT, stop_motion_sync
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

STOP_BURST_DURATION_S = 0.4
# Real-hardware Damp fallback is a single RPC over DDS with no built-in
# retry at the transport layer — unlike the sim burst above, a dropped
# packet here means the e-stop silently did nothing. Retry a few times
# with a short pause rather than trust one packet to land.
REAL_DAMP_MAX_ATTEMPTS = 3
REAL_DAMP_RETRY_DELAY_S = 0.15
# Ceiling on the whole retry loop. Each attempt can sit on the sport client's
# 10 s timeout, so attempt-count alone bounds this at ~30 s — far too long for
# the call an operator makes when something is going wrong. Past this budget we
# stop retrying, and stop waiting on the attempt in flight, and report honestly
# rather than keep trying in silence.
REAL_DAMP_TOTAL_BUDGET_S = 4.0

SIM_MODE = os.environ.get("SIM_MODE", "stub")
#: How long the stop's answer waits to report whether speech stopped.
SPEECH_STOP_WAIT_S = 1.0
# "on_failure" (default): damp only when the zero-velocity burst was not
# acknowledged. "always": damp after every stop, the pre-2026-10-15 behaviour.
DAMP_POLICY = os.environ.get("STOP_EVERYTHING_DAMP", "on_failure").strip().lower()

_T = TypeVar("_T")


def _on_own_thread(fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> asyncio.Future[_T]:
    """Start `fn` now, on a thread of its own, and return a future for its answer.

    Not asyncio.to_thread: that queues on the loop's shared executor, which
    camera streams, voice long-polls and RPCs hung on a bad link can fill. The
    stop waited behind them, and a Damp attempt still queued when its budget
    ran out was cancelled without being sent. Cancelling this future gives up
    only the wait; the call itself always runs.
    """
    done: concurrent.futures.Future[_T] = concurrent.futures.Future()
    done.set_running_or_notify_cancel()

    def work() -> None:
        try:
            done.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            done.set_exception(exc)

    threading.Thread(target=work, name="stop-rpc", daemon=True).start()
    return asyncio.wrap_future(done)


def _zero_burst(height: float) -> Awaitable[tuple[int, int, str | None, float]]:
    """The soft-stop tier: `(sent, acked, error, duration_s)` of a zero burst.

    The burst starts on the call, not on the await, so `run` can start it
    ahead of the hand and speech RPCs and await it after them.
    """
    start = time.monotonic()
    burst: asyncio.Future[tuple[int, int]] = asyncio.get_running_loop().create_future()
    try:
        burst = _on_own_thread(stop_motion_sync, height=height, duration_s=STOP_BURST_DURATION_S)
    except Exception as exc:
        burst.set_exception(exc)

    async def outcome() -> tuple[int, int, str | None, float]:
        try:
            sent, acked = await burst
            return sent, acked, None, time.monotonic() - start
        except Exception as exc:
            # Not fatal, and not the end of the stop: a burst that raised is a
            # burst the robot may never have heard, which is exactly when the
            # Damp fallback in `run` has to run. It used to propagate and skip it.
            log.exception("stop_everything.burst_failed")
            return 0, 0, f"{type(exc).__name__}: {exc}", time.monotonic() - start

    return outcome()


async def soft_stop(height: float = DEFAULT_HEIGHT) -> dict[str, Any]:
    """Halt the base and stay standing. Never damps, never latches.

    Same order as `run` for the parts it shares: gate first, synchronously,
    so Nav2's next tick cannot undo the burst; then the Nav2 goal; then
    cancellation; then zeros.
    Arms and hands are left alone — `stop_everything` is the stop for those.
    """
    try:
        from bridge.sdk.perception_link import get_link

        get_link().disable(reason="soft_stop")
    except Exception:
        log.exception("soft_stop.gate_close_failed")

    # A goal left active walks the robot again the moment the gate is re-armed.
    try:
        from bridge.sdk.nav_link import get_nav_link

        nav = get_nav_link()
        if nav.can_publish:
            nav.cancel(None)
    except Exception:
        log.exception("soft_stop.nav_cancel_failed")

    registry = get_registry()
    cancelled_ids = [t.task_id for t in registry.list_active() if registry.cancel(t.task_id)]
    log.warning("soft_stop.requested", cancelled_task_ids=cancelled_ids)

    sent, acked, error, duration = await _zero_burst(height)
    return {
        "cancelled_task_ids": cancelled_ids,
        "cancelled_count": len(cancelled_ids),
        "stop_burst_duration_s": round(duration, 3),
        "stop_burst_sent": sent,
        "stop_burst_acked": acked if SIM_MODE == "real" else None,
        "stop_burst_error": error,
        # Read by the caller: a soft stop the robot never heard is no stop.
        # The answer then is stop_everything, which damps in exactly this case.
        "heard": error is None and (acked > 0 if SIM_MODE == "real" else sent > 0),
    }


async def run(height: float = DEFAULT_HEIGHT, *, signal: bool = True) -> dict[str, Any]:
    """Cancel all running tasks and send a zero-velocity burst.

    `signal=False` runs a stop another process already stamped (see
    `watch_foreign_stops`) without stamping it again: a newer stamp than the
    one the operator saw makes /safety/ack refuse with 409.
    """
    # Cross-process first, and before any await: the teleop stream runs in its
    # own process with its own TaskRegistry, so the cancellation below cannot
    # reach it. Found on the robot when PARAR left it turning for another 27
    # degrees. See bridge/estop.py.
    sentinel_written = signal_stop() if signal else None
    from bridge import safety

    safety.clear_confirmations("stop_everything")

    # CLOSE THE NAV GATE, and do it here — synchronously, before any await.
    #
    # Without this, "stop" means "pause". The zero-velocity burst below halts
    # the gait, and then Nav2's next tick arrives ~50 ms later, passes through
    # an open gate, and the robot walks on. Every other thing this function does
    # would be undone by a planner that was never told to stop.
    #
    # perception_link.disable() also opens a brake window, so the gap between
    # closing the gate and the firmware's own 1 s SET_VELOCITY deadman is
    # covered by zeros rather than by coasting.
    #
    # Wrapped, and deliberately not allowed to fail the stop: if the perception
    # link is absent or broken there is nothing to close, and refusing to stop
    # the robot because the gate could not be read would be the worst possible
    # trade.
    try:
        from bridge.sdk.perception_link import get_link

        get_link().disable(reason="stop_everything")
    except Exception:
        log.exception("stop_everything.gate_close_failed")

    # ...AND CANCEL THE NAV2 GOAL. The closed gate already stops the legs; this
    # stops Nav2 wanting to move. A goal left active would walk the robot again
    # the moment somebody re-arms the gate, toward a target set before the stop.
    # Ahead of every await, never fatal, like the gate. The navigate_to monitors
    # cancel too (their tasks are cancelled below); this does not depend on a
    # monitor still being alive.
    try:
        from bridge.sdk.nav_link import get_nav_link

        nav = get_nav_link()
        if nav.can_publish:
            nav.cancel(None)
    except Exception:
        log.exception("stop_everything.nav_cancel_failed")

    # LET GO OF THE ARMS, synchronously. If move_arm (or anything else in this
    # process) has the rt/arm_sdk driver engaged, its 50 Hz loop is actively
    # holding a pose — cancelling the task alone starts a ramp-down, but only
    # if the task is still alive to notice. request_release() asks the loop
    # itself, which works even when the task that engaged it is long gone
    # (hold=True is exactly that case). Non-blocking by design: the ramp
    # completes in the driver's own loop while the rest of the stop proceeds.
    # Wrapped, never fatal — a broken teleop import must not break the e-stop.
    arm_sdk_release_requested = False
    try:
        from bridge.teleop.arm_sdk import get_driver as _get_arm_driver

        driver = _get_arm_driver()
        if driver.engaged:
            driver.request_release()
            arm_sdk_release_requested = True
    except Exception:
        log.exception("stop_everything.arm_sdk_release_failed")

    registry = get_registry()
    active = registry.list_active()
    cancelled_ids: list[str] = []
    for task in active:
        if registry.cancel(task.task_id):
            cancelled_ids.append(task.task_id)

    log.warning(
        "stop_everything.requested",
        cancelled_count=len(cancelled_ids),
        cancelled_task_ids=cancelled_ids,
    )

    # Off-thread from here down. Every cancel_event above is already set, so
    # the skills are stopping themselves whatever happens next; what this buys
    # is an event loop that can still answer a second stop press or a state
    # poll while these blocking DDS calls are in flight.
    # THE ZERO BURST FIRST: the zero setpoints are what stop the robot, so the
    # hand and voice RPCs below start beside it, never ahead of it. A slow
    # BrainCo write awaited here used to hold the first setpoint.
    burst = _zero_burst(height)

    # OPEN THE HANDS. A BrainCo has no firmware dead-man (teleop/hands.py) — a
    # grip commanded via set_hand outlives every task and both bursts below,
    # so the stop is the release path of last resort the hands module demands.
    # Off-thread (it publishes DDS) and never fatal, same as the arms above.
    # NullHandDriver makes this a no-op wherever hands are unconfigured.
    hand_relax: asyncio.Future[None] | None = None
    try:
        from bridge.teleop.hands import get_driver as _get_hand_driver

        hand_driver = _get_hand_driver()
        if hand_driver.sides:
            hand_relax = _on_own_thread(hand_driver.relax)
    except Exception:
        log.exception("stop_everything.hand_relax_failed")

    # STOP TALKING. Our own Spanish speech only (PlayStream under app "c3po");
    # firmware TTS and the co-tenant's assistant cannot be stopped from here.
    speech_stop: asyncio.Future[tuple[int, str | None]] | None = None
    if SIM_MODE == "real":
        try:
            from bridge.sdk import g1_rpc as _g1_rpc

            _g1_rpc.cancel_pcm_playback()
            speech_stop = _on_own_thread(_g1_rpc.stop_play)
        except Exception:
            log.exception("stop_everything.speech_stop_failed")

    burst_sent, burst_acked, burst_error, duration = await burst

    # Zero velocity stops the gait and leaves the robot balanced; damp zeroes
    # joint stiffness, which drops a standing robot. So damp only when the
    # robot did not acknowledge a single zero setpoint (or the burst raised),
    # unless STOP_EVERYTHING_DAMP=always — see the module docstring.
    real_damp_rpc_code: int | None = None
    real_damp_attempts = 0
    damp_reason: str | None = None
    if SIM_MODE == "real":
        if DAMP_POLICY == "always":
            damp_reason = "policy_always"
        elif burst_error is not None:
            damp_reason = "burst_failed"
        elif burst_acked == 0:
            damp_reason = "burst_unacknowledged"
    if damp_reason is not None:
        from unitree_sdk2py.rpc.internal import RPC_ERR_CLIENT_API_TIMEOUT

        from bridge.sdk import g1_protocol, g1_rpc

        budget_exhausted = False
        try:
            # One deadline over the whole loop, not a wait_for per attempt: a
            # wait_for given no time left (the budget ran out in the pause
            # before a retry) cancels its attempt before it is sent.
            async with asyncio.timeout(REAL_DAMP_TOTAL_BUDGET_S):
                for attempt in range(1, REAL_DAMP_MAX_ATTEMPTS + 1):
                    real_damp_attempts = attempt
                    # What an attempt the budget stops waiting for reports. One
                    # can sit on the sport client's 10 s timeout; it is sent
                    # anyway and finishes on its own thread.
                    real_damp_rpc_code = RPC_ERR_CLIENT_API_TIMEOUT
                    real_damp_rpc_code, _ = await _on_own_thread(
                        g1_rpc.call_sport, g1_protocol.Mode.DAMP
                    )
                    if real_damp_rpc_code == 0:
                        break
                    log.warning(
                        "stop_everything.real_damp_fallback.retry",
                        attempt=attempt,
                        rpc_code=real_damp_rpc_code,
                    )
                    if attempt < REAL_DAMP_MAX_ATTEMPTS:
                        await asyncio.sleep(REAL_DAMP_RETRY_DELAY_S)
        except TimeoutError:
            budget_exhausted = True
            log.warning(
                "stop_everything.real_damp_fallback.budget_exhausted",
                attempts=real_damp_attempts,
                budget_s=REAL_DAMP_TOTAL_BUDGET_S,
            )
        log.warning(
            "stop_everything.real_damp_fallback",
            reason=damp_reason,
            rpc_code=real_damp_rpc_code,
            attempts=real_damp_attempts,
            succeeded=real_damp_rpc_code == 0,
            budget_exhausted=budget_exhausted,
        )

    # Awaited after Damp, which must not wait on a slow hand write either.
    hands_relaxed = False
    if hand_relax is not None:
        try:
            await hand_relax
            hands_relaxed = True
        except Exception:
            log.exception("stop_everything.hand_relax_failed")

    speech_stop_rpc_code: int | None = None
    if speech_stop is not None:
        try:
            speech_stop_rpc_code, _ = await asyncio.wait_for(
                asyncio.shield(speech_stop), SPEECH_STOP_WAIT_S
            )
        except Exception:
            # Slow or failed: the stop's answer must not wait on the speaker.
            log.warning("stop_everything.speech_stop_unconfirmed", exc_info=True)

    return {
        "cancelled_task_ids": cancelled_ids,
        "cancelled_count": len(cancelled_ids),
        "speech_stop_rpc_code": speech_stop_rpc_code,
        "stop_burst_duration_s": round(duration, 3),
        "stop_burst_sent": burst_sent,
        "stop_burst_acked": burst_acked if SIM_MODE == "real" else None,
        "stop_burst_error": burst_error,
        "arm_sdk_release_requested": arm_sdk_release_requested,
        "hands_relaxed": hands_relaxed,
        "real_damp_fallback_rpc_code": real_damp_rpc_code,
        "real_damp_fallback_attempts": real_damp_attempts,
        # None when Damp did not run (sim, or the stop landed): the console
        # reads only `False` as a failed stop.
        "real_damp_fallback_succeeded": (
            (real_damp_rpc_code == 0) if damp_reason is not None else None
        ),
        "real_damp_fallback_reason": damp_reason,
        # False: this process is latched, but the teleop stream reads only the
        # sentinel file and never heard this stop. A driving headset may still drive.
        "sentinel_written": sentinel_written,
    }


#: How often the MCP process looks for a stop another process signalled.
FOREIGN_STOP_POLL_S = 0.1


async def watch_foreign_stops() -> None:
    """Run the stop here for a PARAR another process signalled, until cancelled.

    The headset's PARAR down the teleop socket only stamps the sentinel
    (teleop/server.py `raise_stop`). That latches this process, but an arm
    move, a walk_to or a Nav2 monitor already running here went on, and with
    apps/back down nothing else stops them. A new stamp more than 1 ms away from
    this process's own last stop is someone else's. A stamp from before the
    watcher started is already latched and is not run again, and each stamp
    is run once, failed or not: retrying it would resend Damp every poll.

    Any change of stamp is a new stop, not only a later one: after the wall
    clock steps back, every new stamp is older than the last, and ordering
    them ignored every headset PARAR.
    """
    seen = estop._file_stop_at()
    while True:
        await asyncio.sleep(FOREIGN_STOP_POLL_S)
        stamp = estop._file_stop_at()
        if stamp == seen or not stamp:
            continue  # unchanged, or no file to read (deleted, or a failed stat)
        seen = stamp
        if abs(stamp - estop._signalled_at) <= 1e-3:
            continue  # this process's own stop: its run() did the work
        log.warning("stop_everything.foreign_stop", stamped_at=stamp)
        try:
            await run(signal=False)
        except Exception:
            log.exception("stop_everything.foreign_stop_failed")


@contextlib.asynccontextmanager
async def watching_foreign_stops() -> AsyncIterator[None]:
    """`watch_foreign_stops` for as long as the server serves.

    Process-wide, not FastMCP's lifespan: over streamable HTTP that is entered
    once per MCP session, so with apps/back down there would be no watcher.
    Not in stub, where the stop_everything tool itself does nothing.
    """
    if SIM_MODE == "stub":
        yield
        return
    watcher = asyncio.create_task(watch_foreign_stops())
    try:
        yield
    finally:
        watcher.cancel()
