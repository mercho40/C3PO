"""Real-G1 high-level RPC client — plain DDS request/response, no WebRTC.

`unitree_sdk2py.rpc.client.Client` is generic RPC-over-DDS infrastructure
(Go2's `SportClient` is built on the same base). A service name `"sport"`
resolves to `rt/api/sport/request` / `rt/api/sport/response` via
`GetClientChannelName` — exactly the topics `g1_protocol.REAL_TOPICS`
already names.

Each service exposes *many* api_ids — `sport` spans 7001..7107 (see
`g1_protocol` and docs/ROBOT-API.md §2). Most posture/gesture calls
happen to share a `{"data": N}` parameter shape, but that is a property of
those particular calls, not of the service: `SET_VELOCITY` takes
`{"velocity": [...], "duration": d}`. So a client registers every api_id it
intends to use and callers pass the parameter JSON.
"""

from __future__ import annotations

import json
from typing import Final

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


_voice_client: _G1Client | None = None
_tts_index = 0


def _get_voice_client() -> _G1Client:
    global _voice_client
    if _voice_client is None:
        _voice_client = _G1Client(
            g1_protocol.VOICE_SERVICE,
            (
                g1_protocol.API_ID_VOICE_TTS,
                g1_protocol.API_ID_VOICE_START_PLAY,
                g1_protocol.API_ID_VOICE_STOP_PLAY,
                g1_protocol.API_ID_VOICE_GET_VOLUME,
                g1_protocol.API_ID_VOICE_SET_VOLUME,
            ),
            # TTS synthesises before it answers, so size this like the arm
            # service rather than the sport one: it acks on completion, not on
            # receipt. See the ARM_TIMEOUT_S note above — we have already been
            # caught once reading a slow ack as a failure.
            timeout_s=ARM_TIMEOUT_S,
        )
        _voice_client._SetApiVerson(g1_protocol.VOICE_API_VERSION)
        _voice_client.Init()
    return _voice_client


def speak(text: str, speaker_id: int = g1_protocol.Speaker.ENGLISH) -> tuple[int, str | None]:
    """Speak `text` aloud on the robot (voice service, api_id 1001).

    Deliberately not `unitree_sdk2py.g1.audio.AudioClient.TtsMaker`, which
    carries a vendor bug: `self.tts_index += self.tts_index` starting from 0,
    so `index` is 0 on every call forever. If the firmware dedupes on that
    index — and an index field with no other purpose suggests it does — the
    second and later utterances are silently dropped, which would present as
    "TTS randomly stops working". Verified present in our installed copy.

    We send the same api_id with a monotonically increasing index instead.
    """
    global _tts_index
    _tts_index += 1
    param = json.dumps({"index": _tts_index, "text": text, "speaker_id": speaker_id})
    return _get_voice_client().call_raw(g1_protocol.API_ID_VOICE_TTS, param)


def get_volume() -> tuple[int, str | None]:
    """Read the robot's speaker volume. Getter, safe."""
    return _get_voice_client().call_raw(g1_protocol.API_ID_VOICE_GET_VOLUME, json.dumps({}))


# Our own app_name, and it is not cosmetic. PlayStop is scoped by app_name, so
# this is the reason gemm-ai cannot stop our speech and we cannot stop theirs —
# they publish on this same service as "gemm-ai". Sharing a name would let two
# stacks silence each other with no way to tell which did it.
PLAY_APP_NAME: Final[str] = "c3po"

# 1 s of 16 kHz mono 16-bit. The on-robot example's "96000 bytes (3 s)" is a
# convention, not a protocol rule — the official example passes a whole ~5 s WAV
# in one call. Smaller chunks are chosen for latency to first sound, not for
# safety: chunks sharing a stream_id concatenate gaplessly, so this costs
# nothing but gets speech started sooner.
PLAY_CHUNK_BYTES: Final[int] = 32000


def play_pcm(
    pcm: bytes,
    stream_id: str,
    app_name: str = PLAY_APP_NAME,
) -> tuple[int, str | None]:
    """Push 16 kHz mono 16-bit PCM to the speaker (voice/1003 START_PLAY).

    This is how the robot speaks Spanish. The firmware TTS has no Spanish voice
    and answers rpc_code 0 while emitting noise (D6.1), so anything not Chinese
    or English is synthesised off-board and arrives here as PCM.

    `stream_id` IS THE INTERRUPT MODEL, per the vendor: the *same* id continues
    playback from cache, a *different* id interrupts whatever is playing. So
    every chunk of one utterance must reuse one id — that is what makes them
    concatenate instead of each one cutting off the last — and barging in means
    calling again with a NEW id, with no PlayStop first.

    Format is not negotiable: both vendor examples hard-reject anything but
    16 kHz mono 16-bit, and stereo is documented as causing playback issues.

    Returns the LAST chunk's (rpc_code, data), and stops at the first failure so
    a rejected format does not spend thirty more calls being rejected.
    """
    if not pcm:
        return 0, None

    client = _get_voice_client()
    param = json.dumps({"app_name": app_name, "stream_id": stream_id})
    code, data = 0, None
    for offset in range(0, len(pcm), PLAY_CHUNK_BYTES):
        chunk = pcm[offset : offset + PLAY_CHUNK_BYTES]
        code, data = client._CallRequestWithParamAndBin(
            g1_protocol.API_ID_VOICE_START_PLAY, param, chunk
        )
        if code != 0:
            log.warning(
                "voice.play_pcm.chunk_failed",
                rpc_code=code,
                offset=offset,
                total=len(pcm),
                hint="100 is the service's only declared error: Invalid parameter",
            )
            return code, data
    return code, data


def stop_play(app_name: str = PLAY_APP_NAME) -> tuple[int, str | None]:
    """Stop OUR playback (voice/1004). Scoped by app_name, not stream_id.

    Three of four sources agree it takes app_name; the on-robot C++ example
    passing a stream_id is simply wrong. The scoping is a feature here — it is
    structurally impossible for this to silence the co-tenant's assistant.
    """
    return _get_voice_client().call_raw(
        g1_protocol.API_ID_VOICE_STOP_PLAY, json.dumps({"app_name": app_name})
    )


_motion_switcher_client: _G1Client | None = None


def _get_motion_switcher_client() -> _G1Client:
    global _motion_switcher_client
    if _motion_switcher_client is None:
        # ONLY CHECK_MODE is registered. SELECT_MODE and RELEASE_MODE transfer
        # ownership of the robot between controllers, and `_RegistApi` is what
        # makes an api_id sendable at all — leaving them unregistered means a
        # future caller cannot reach them by accident. Registering an api_id
        # you do not intend to send is how it gets sent eventually.
        _motion_switcher_client = _G1Client(
            g1_protocol.MOTION_SWITCHER_SERVICE,
            (g1_protocol.API_ID_MS_CHECK_MODE,),
            timeout_s=SPORT_TIMEOUT_S,
        )
        _motion_switcher_client._SetApiVerson(g1_protocol.MOTION_SWITCHER_API_VERSION)
        _motion_switcher_client.Init()
    return _motion_switcher_client


def check_motion_mode() -> tuple[int, dict[str, str] | None]:
    """Ask `motion_switcher` which controller currently owns the robot.

    Returns `(rpc_code, {"name": ..., "form": ...})`, or `(code, None)` if the
    service declined or answered unparseably.

    An **empty `name` means no motion controller is loaded** — the robot is in
    what the vendor calls debug mode. Observed live 2026-08-14: `{'form': '0',
    'name': ''}` while `SetFsmId` returned `code 0` and did nothing, and the
    FSM getters (7001/7002) returned nothing at all, so `get_state` reported
    `fsm_id=None` and `posture=unknown`.

    Note this takes `"{}"`, not the empty string the sport getters take.
    """
    code, data = _get_motion_switcher_client().call_raw(
        g1_protocol.API_ID_MS_CHECK_MODE, json.dumps({})
    )
    if code != 0 or not data:
        log.warning("g1_rpc.check_motion_mode_failed", rpc_code=code)
        return code, None
    try:
        parsed = json.loads(data)
    except (ValueError, TypeError) as exc:
        log.warning("g1_rpc.check_motion_mode_unparseable", data=data, error=str(exc))
        return code, None
    return code, {"name": str(parsed.get("name", "")), "form": str(parsed.get("form", ""))}


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
