"""Real-G1 high-level RPC client — plain DDS request/response, no WebRTC.

`unitree_sdk2py.rpc.client.Client` is generic RPC-over-DDS infrastructure
(Go2's `SportClient` is built on the same base). A service name `"sport"`
resolves to `rt/api/sport/request` / `rt/api/sport/response` via
`GetClientChannelName` — exactly the topics `g1_protocol.REAL_TOPICS`
already names. Unlike Go2 (one api_id per action), G1 uses a single api_id
per service (7101 posture, 7106 arm gesture) with `{"data": N}` selecting
the mode/gesture.
"""

from __future__ import annotations

from unitree_sdk2py.rpc.client import Client

from bridge.sdk import g1_protocol


class _G1Client(Client):
    def __init__(self, service_name: str, api_id: int) -> None:
        super().__init__(service_name)
        self._api_id = api_id

    def Init(self) -> None:
        self._RegistApi(self._api_id, 0)

    def call(self, data: int) -> tuple[int, str | None]:
        return self._Call(self._api_id, f'{{"data":{data}}}')


_sport_client: _G1Client | None = None
_arm_client: _G1Client | None = None


def _get_sport_client() -> _G1Client:
    global _sport_client
    if _sport_client is None:
        _sport_client = _G1Client("sport", g1_protocol.API_ID_G1_STATE)
        _sport_client.Init()
    return _sport_client


def _get_arm_client() -> _G1Client:
    global _arm_client
    if _arm_client is None:
        _arm_client = _G1Client("arm", g1_protocol.API_ID_G1_UPPER_LIMBS)
        _arm_client.Init()
    return _arm_client


def call_sport(mode: int) -> tuple[int, str | None]:
    """Send a full-body posture/mode request (api_id=7101). Returns (code, data)."""
    return _get_sport_client().call(mode)


def call_arm(gesture: int) -> tuple[int, str | None]:
    """Send an upper-limb gesture request (api_id=7106). Returns (code, data)."""
    return _get_arm_client().call(gesture)
