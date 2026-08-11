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

from unitree_sdk2py.rpc.client import Client

from bridge.sdk import g1_protocol


class _G1Client(Client):
    def __init__(self, service_name: str, api_ids: tuple[int, ...]) -> None:
        super().__init__(service_name)
        self._api_ids = api_ids

    def Init(self) -> None:
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
            (g1_protocol.API_ID_G1_STATE, g1_protocol.API_ID_LOCO_SET_VELOCITY),
        )
        _sport_client.Init()
    return _sport_client


def _get_arm_client() -> _G1Client:
    global _arm_client
    if _arm_client is None:
        _arm_client = _G1Client("arm", (g1_protocol.API_ID_G1_UPPER_LIMBS,))
        _arm_client.Init()
    return _arm_client


def call_sport(mode: int) -> tuple[int, str | None]:
    """Send a full-body posture/mode request (api_id=7101). Returns (code, data)."""
    return _get_sport_client().call(g1_protocol.API_ID_G1_STATE, mode)


def call_arm(gesture: int) -> tuple[int, str | None]:
    """Send an upper-limb gesture request (api_id=7106). Returns (code, data)."""
    return _get_arm_client().call(g1_protocol.API_ID_G1_UPPER_LIMBS, gesture)


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
    """
    parameter = json.dumps(
        {"velocity": [float(vx), float(vy), float(omega)], "duration": float(duration)}
    )
    return _get_sport_client().call_raw(g1_protocol.API_ID_LOCO_SET_VELOCITY, parameter)
