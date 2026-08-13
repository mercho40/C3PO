"""Real-G1 high-level RPC client — plain DDS request/response, no WebRTC.

`unitree_sdk2py.rpc.client.Client` is generic RPC-over-DDS infrastructure
(Go2's `SportClient` is built on the same base). A service name `"sport"`
resolves to `rt/api/sport/request` / `rt/api/sport/response` via
`GetClientChannelName` — exactly the topics `g1_protocol.REAL_TOPICS`
already names. Unlike Go2 (dozens of api_ids on one client), G1's "sport"
service carries a handful of api_ids we care about — 7101 posture and 7105
SetVelocity so far — each with its own param shape; "arm" carries 7106
gesture. One client per *service name*, multiple api_ids registered on it,
matching how the real SDK's SportClient/LocoClient register many api_ids
on a single client instance.
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

    def call(self, api_id: int, param_json: str) -> tuple[int, str | None]:
        return self._Call(api_id, param_json)


_sport_client: _G1Client | None = None
_arm_client: _G1Client | None = None


def _get_sport_client() -> _G1Client:
    global _sport_client
    if _sport_client is None:
        _sport_client = _G1Client(
            "sport", (g1_protocol.API_ID_G1_STATE, g1_protocol.API_ID_G1_SET_VELOCITY)
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
    return _get_sport_client().call(g1_protocol.API_ID_G1_STATE, f'{{"data":{mode}}}')


def call_arm(gesture: int) -> tuple[int, str | None]:
    """Send an upper-limb gesture request (api_id=7106). Returns (code, data)."""
    return _get_arm_client().call(g1_protocol.API_ID_G1_UPPER_LIMBS, f'{{"data":{gesture}}}')


def call_velocity(vx: float, vy: float, vyaw: float, duration: float) -> tuple[int, str | None]:
    """Send an open-loop body-frame velocity command (api_id=7105, SetVelocity).

    Unlike walk_to/turn, this never reads back pose — it's the same
    fire-and-forget pattern xr_teleoperate's own controller-button
    locomotion uses (see bridge.skills.walk_velocity for the safety-clamped
    skill wrapper; call this directly only if you're doing your own
    clamping/stop handling).
    """
    param = json.dumps({"velocity": [vx, vy, vyaw], "duration": duration})
    return _get_sport_client().call(g1_protocol.API_ID_G1_SET_VELOCITY, param)
