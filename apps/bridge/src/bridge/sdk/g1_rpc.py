"""Real-G1 high-level RPC client — plain DDS request/response, no WebRTC.

`unitree_sdk2py.rpc.client.Client` is generic RPC-over-DDS infrastructure
(Go2's `SportClient` is built on the same base). A service name `"sport"`
resolves to `rt/api/sport/request` / `rt/api/sport/response` via
`GetClientChannelName` — exactly the topics `g1_protocol.REAL_TOPICS`
already names.

Each service exposes *many* api_ids — `sport` spans 7001..7107 (see
`g1_protocol` and docs/ROBOT-INVENTORY.md §3). Most posture/gesture calls
happen to share a `{"data": N}` parameter shape, but that is a property of
those particular calls, not of the service: `SET_VELOCITY` takes
`{"velocity": [...], "duration": d}`. So a client registers every api_id it
intends to use and callers pass the parameter JSON.
"""

from __future__ import annotations

import json

import structlog
from unitree_sdk2py.rpc.client import Client

from bridge.sdk import g1_protocol

log = structlog.get_logger(__name__)

# These services ack on *completion of the motion*, not on receipt of the
# request. Measured on hardware 2026-08-11: a HIGH_WAVE gesture answered
# `code=0` after **4.19 s**. The SDK's default timeout is far shorter, so every
# gesture came back RPC_ERR_CLIENT_API_TIMEOUT (3104) while the robot was
# visibly, correctly performing it — a false failure in the worst direction,
# since an operator or LLM reading it would conclude the robot ignored them.
#
# So these timeouts must exceed the duration of the longest motion the service
# performs, not the network round-trip. Gestures are the slow ones; postures
# (sit, lie_up, squat) are physical transitions too and get the same headroom.
ARM_TIMEOUT_S = 15.0
SPORT_TIMEOUT_S = 10.0


class _G1Client(Client):
    def __init__(
        self, service_name: str, api_ids: tuple[int, ...], timeout_s: float | None = None
    ) -> None:
        super().__init__(service_name)
        self._api_ids = api_ids
        self._timeout_s = timeout_s

    def Init(self) -> None:
        if self._timeout_s is not None:
            self.SetTimeout(self._timeout_s)
        for api_id in self._api_ids:
            self._RegistApi(api_id, 0)

    def call_raw(self, api_id: int, parameter: str) -> tuple[int, str | None]:
        return self._Call(api_id, parameter)

    def call(self, api_id: int, data: int) -> tuple[int, str | None]:
        return self.call_raw(api_id, f'{{"data":{data}}}')


_sport_client: _G1Client | None = None
_arm_client: _G1Client | None = None


def _get_sport_client() -> _G1Client:
    global _sport_client
    if _sport_client is None:
        # One client per service, registering every api_id we use on it —
        # separate Client instances on the same service would each stand up
        # their own request/response channels.
        _sport_client = _G1Client(
            "sport",
            (
                g1_protocol.API_ID_G1_STATE,
                g1_protocol.API_ID_LOCO_SET_VELOCITY,
                g1_protocol.API_ID_LOCO_SET_BALANCE_MODE,
                g1_protocol.API_ID_LOCO_GET_FSM_ID,
                g1_protocol.API_ID_LOCO_GET_FSM_MODE,
            ),
            timeout_s=SPORT_TIMEOUT_S,
        )
        _sport_client.Init()
    return _sport_client


def _get_arm_client() -> _G1Client:
    global _arm_client
    if _arm_client is None:
        _arm_client = _G1Client(
            "arm", (g1_protocol.API_ID_G1_UPPER_LIMBS,), timeout_s=ARM_TIMEOUT_S
        )
        _arm_client.Init()
    return _arm_client


def call_sport(mode: int) -> tuple[int, str | None]:
    """Send a full-body posture/mode request (api_id=7101). Returns (code, data)."""
    return _get_sport_client().call(g1_protocol.API_ID_G1_STATE, mode)


def call_sport_api(api_id: int, data: int) -> tuple[int, str | None]:
    """Send any registered sport-service request. Returns (code, data).

    The sport service is not one api_id: 7101 sets the FSM, 7102 the balance
    mode, 7105 a velocity. Callers that dispatch from the skill catalogue must
    pass the api_id the catalogue names rather than assuming 7101, or a skill
    like `balance_stand` silently becomes a posture change.

    The api_id must appear in `_get_sport_client()`'s registration tuple —
    `_RegistApi` is what binds it on the client, so an unregistered id fails
    rather than reaching the robot.
    """
    return _get_sport_client().call(api_id, data)


def call_arm(gesture: int) -> tuple[int, str | None]:
    """Send an upper-limb gesture request (api_id=7106). Returns (code, data)."""
    return _get_arm_client().call(g1_protocol.API_ID_G1_UPPER_LIMBS, gesture)


def _call_int_getter(api_id: int) -> int | None:
    """Run a GET api_id that answers `{"data": N}`. Returns None on any failure.

    The vendor's getters take an *empty* parameter, not `{}` — see
    `g1_loco_client.hpp`, which builds a bare Request and reads `js["data"]`
    off the reply.

    Returns None rather than raising: callers use these for reporting, and a
    firmware that declines one (7003 answers code 7301 on this robot) should
    degrade to "unknown", not break `get_state`.
    """
    try:
        code, data = _get_sport_client().call_raw(api_id, "")
    except Exception as exc:
        log.warning("g1_rpc.getter_failed", api_id=api_id, error=str(exc))
        return None
    if code != 0 or not data:
        log.debug("g1_rpc.getter_nonzero", api_id=api_id, rpc_code=code)
        return None
    try:
        return int(json.loads(data)["data"])
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("g1_rpc.getter_unparseable", api_id=api_id, data=data, error=str(exc))
        return None


def get_fsm_id() -> int | None:
    """Current FSM state index (api_id=7001), or None if unavailable."""
    return _call_int_getter(g1_protocol.API_ID_LOCO_GET_FSM_ID)


def get_fsm_mode() -> int | None:
    """Current FSM sub-mode (api_id=7002), or None if unavailable."""
    return _call_int_getter(g1_protocol.API_ID_LOCO_GET_FSM_MODE)


def call_set_velocity(
    vx: float, vy: float, omega: float, duration: float
) -> tuple[int, str | None]:
    """Send a body-frame velocity setpoint (api_id=7105). Returns (code, data).

    `duration` is how long the firmware honours this setpoint before stopping
    on its own. Keep it small and re-issue at loop rate: it is the robot's
    built-in deadman, and the reason a crashed bridge stops the robot instead
    of leaving it walking. See `_locomotion.VELOCITY_DURATION_S`.

    This is a high-level setpoint — the same one the joystick produces. The
    G1's own controller decides how to walk; we are not doing leg control.

    **Known hazard, not yet resolved on hardware.** This call currently *waits
    for an ack*, and `_locomotion.send_velocity` issues it at 50 Hz inside the
    walk_to/turn control loop. Velocity acked promptly when measured (unlike
    gestures, which ack on completion — see ARM_TIMEOUT_S), so it works today,
    but any per-call latency multiplies by the loop rate, and if the firmware
    ever answers slowly the loop stalls with SPORT_TIMEOUT_S of headroom to
    burn. The vendor's own C++ client blocks up to 5 s per call, which is why
    the colleague's cmd_vel_to_loco bridge went fire-and-forget for exactly
    this path.

    The likely fix is `_CallNoReply` for velocity only, keeping request/response
    for postures and gestures where the ack is the completion signal. Left
    unchanged for now because it touches the motion path and could not be
    verified — measure loop rate on hardware before switching.
    """
    parameter = json.dumps(
        {"velocity": [float(vx), float(vy), float(omega)], "duration": float(duration)}
    )
    return _get_sport_client().call_raw(g1_protocol.API_ID_LOCO_SET_VELOCITY, parameter)
