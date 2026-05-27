"""WebRTC client for the Unitree Go2 — legacy `/offer` SDP path.

Targets the older firmware (Go2 ≤ 1.1.14) which exposes a plain HTTP
SDP exchange on port 8081 with no encryption. The newer V3 firmware
(Go2 ≥ 1.1.15, G1 ≥ 1.5.1) wraps the SDP in AES-128-GCM and runs on
port 9991 — handled in a follow-up (see `exchangeSdpNew` in
legion1581/unitree_ui/src/connection/local-connector.ts for the spec).

Protocol layout (all message envelopes ride the single `data` DataChannel
as JSON strings):

    Robot → Client:
        {"type": "validation", "data": "<hex_challenge>"}      first message
        {"type": "validation", "data": "Validation Ok."}        after our reply
        {"type": "msg",  "topic": "rt/lf/lowstate", "data": {...}}
        {"type": "res",  "topic": "rt/api/sport/response", "data": {...}}
        {"type": "errors" | "add_error" | "rm_error", "data": [...]}
        {"type": "heartbeat", "topic": "", "data": {...}}        echoes
        {"type": "rtc_inner_req", "info": {"req_type": ...}}     RTT probes etc

    Client → Robot:
        {"type": "validation", "topic": "", "data": "<b64(md5)>"}  challenge reply
        {"type": "subscribe", "topic": "rt/lf/lowstate"}            start a stream
        {"type": "unsubscribe", "topic": ...}
        {"type": "req", "topic": "rt/api/sport/request",
         "data": {"header": {"identity": {"id": N, "api_id": 1008}},
                  "parameter": "{\"x\":0.3,...}", "binary": []}}
        {"type": "heartbeat", "topic": "", "data": {"timeInStr": ..., "timeInNum": ...}}

Validation challenge response:
    md5_hex = md5("UnitreeGo2_" + challenge_hex).hexdigest()
    response = base64(bytes.fromhex(md5_hex))

This file is structural: it builds an aiortc PeerConnection, runs the
SDP exchange, wires the DataChannel handlers, and exposes a small
async API (`connect`, `subscribe`, `publish_typed`, `publish_request`,
`close`). Skills can layer on top.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
import structlog
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Wire-format constants
# ---------------------------------------------------------------------------

MSG_VALIDATION = "validation"
MSG_SUBSCRIBE = "subscribe"
MSG_UNSUBSCRIBE = "unsubscribe"
MSG_MSG = "msg"
MSG_REQUEST = "req"
MSG_RESPONSE = "res"
MSG_VID = "vid"
MSG_AUD = "aud"
MSG_ERR = "err"
MSG_ERRORS = "errors"
MSG_ADD_ERROR = "add_error"
MSG_RM_ERROR = "rm_error"
MSG_HEARTBEAT = "heartbeat"
MSG_RTC_INNER_REQ = "rtc_inner_req"
MSG_RTC_REPORT = "rtc_report"

# Legacy firmware (no SDP encryption)
LEGACY_PORT = 8081
LEGACY_PATH = "/offer"

# New firmware (AES-128-GCM SDP exchange — not yet implemented here)
NEW_PORT = 9991

VALIDATION_PREFIX = "UnitreeGo2_"
HEARTBEAT_PERIOD_S = 2.0
HEARTBEAT_DATA_TYPES = (MSG_HEARTBEAT,)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

TopicHandler = Callable[[str, Any], None | Awaitable[None]]
"""Called for every `msg` / `res` arrival on a subscribed topic.

Signature: `(topic, data) -> None | Awaitable[None]`. Sync handlers run
inline; coroutines are scheduled on the loop.
"""


@dataclass
class Go2ConnectionConfig:
    """Where to find the robot + how aggressively to retry."""

    host: str  # IP address or hostname on the LAN
    port: int = LEGACY_PORT
    path: str = LEGACY_PATH
    sdp_timeout_s: float = 10.0
    validation_timeout_s: float = 20.0
    heartbeat_period_s: float = HEARTBEAT_PERIOD_S


@dataclass
class _PendingRequest:
    """One outstanding `req` awaiting a `res` from the robot."""

    future: asyncio.Future
    api_id: int
    topic: str
    sent_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validation_response(challenge_hex: str) -> str:
    """Compute the challenge reply: base64(md5("UnitreeGo2_" + challenge))."""
    digest = hashlib.md5((VALIDATION_PREFIX + challenge_hex).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _new_request_id() -> int:
    """Random positive int32 — matches legion1581's `Math.floor(Math.random() * 2^31)`."""
    return random.randint(0, 2_147_483_646)


def _now_heartbeat() -> dict[str, Any]:
    now = time.time()
    lt = time.localtime(now)
    time_str = (
        f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d} "
        f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}"
    )
    return {"timeInStr": time_str, "timeInNum": int(now)}


# ---------------------------------------------------------------------------
# WebRTC client
# ---------------------------------------------------------------------------


class Go2WebRTCClient:
    """Connects to a Unitree Go2 over WebRTC and exposes its DDS topics.

    Lifecycle:
        client = Go2WebRTCClient(Go2ConnectionConfig(host="192.168.123.161"))
        client.on_message(handler)
        await client.connect()         # SDP + validation + heartbeat started
        client.subscribe("rt/lf/lowstate")
        await client.publish_request("rt/api/sport/request", 1008, '{"x":0.3,"y":0,"z":0}')
        ...
        await client.close()

    Single-connection: rebuild a new client to reconnect.
    """

    def __init__(self, config: Go2ConnectionConfig) -> None:
        self._config = config
        self._pc: RTCPeerConnection | None = None
        self._channel: Any = None  # aiortc DataChannel
        self._validated = asyncio.Event()
        self._closed = asyncio.Event()
        self._heartbeat_task: asyncio.Task | None = None
        self._topic_handlers: list[TopicHandler] = []
        self._error_handlers: list[Callable[[str, Any], None]] = []
        self._pending_requests: dict[int, _PendingRequest] = {}
        # Buffer `subscribe`s issued before validation completes.
        self._pre_validation_queue: list[dict[str, Any]] = []

    # ── public API ─────────────────────────────────────────────────

    def on_message(self, handler: TopicHandler) -> None:
        """Register a handler for every topic message that arrives."""
        self._topic_handlers.append(handler)

    def on_error_message(self, handler: Callable[[str, Any], None]) -> None:
        """Register a handler for `errors` / `add_error` / `rm_error` wire messages."""
        self._error_handlers.append(handler)

    async def connect(self) -> None:
        """Negotiate SDP, open the DataChannel, complete validation."""
        log.info("go2.webrtc.connect.start", host=self._config.host, port=self._config.port)

        # Empty iceServers — we're on the robot's local network (often AP
        # mode with no internet) so STUN discovery would just time out.
        # The robot's SDP answer carries the host candidate we need.
        self._pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))

        @self._pc.on("iceconnectionstatechange")
        def _on_ice_state():
            log.info("go2.webrtc.ice.state", state=self._pc.iceConnectionState)

        @self._pc.on("connectionstatechange")
        def _on_conn_state():
            log.info("go2.webrtc.conn.state", state=self._pc.connectionState)
        # Same transceiver shape as the official mobile app + legion1581 UI.
        self._pc.addTransceiver("video", direction="recvonly")
        self._pc.addTransceiver("audio", direction="sendrecv")
        self._channel = self._pc.createDataChannel("data", ordered=True)
        self._channel.on("open", self._on_channel_open)
        self._channel.on("close", self._on_channel_close)
        self._channel.on("message", self._on_channel_message)

        @self._channel.on("error")
        def _on_channel_error(error: Any) -> None:
            log.error("go2.webrtc.channel.error", error=repr(error))

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)

        # Filter our local SDP to only candidates on the robot's subnet — aiortc
        # gathers from every interface, and pairs that traverse non-routable
        # interfaces stick in IN_PROGRESS and starve the working pair.
        local_sdp = self._filter_sdp_to_subnet(self._pc.localDescription.sdp)

        if self._config.port == NEW_PORT:
            from bridge.sdk.transport.webrtc_v3_sdp import exchange_sdp_v3

            v3 = await exchange_sdp_v3(
                self._config.host,
                local_sdp,
                port=self._config.port,
                sdp_timeout_s=self._config.sdp_timeout_s,
            )
            answer_sdp = v3.answer_sdp
        else:
            answer_sdp = await self._exchange_sdp_legacy(local_sdp)

        # Filter the answer too — drop ICE candidates not on the robot's subnet
        # (the robot advertises link-local 169.254.x.x candidates that never work)
        # and strip `a=ice-options:trickle` so aiortc doesn't wait for trickle
        # candidates that never arrive.
        answer_sdp = self._filter_sdp_to_subnet(answer_sdp)
        answer_sdp = "\n".join(
            line for line in answer_sdp.splitlines() if not line.startswith("a=ice-options:")
        )
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer"),
        )
        log.info("go2.webrtc.sdp.done")

        # Wait for validation to complete before returning.
        try:
            await asyncio.wait_for(
                self._validated.wait(),
                timeout=self._config.validation_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            await self.close()
            raise TimeoutError(
                f"Go2 did not validate within {self._config.validation_timeout_s}s — "
                "is firmware ≥ 1.1.15? Then we need the new SDP path."
            ) from exc

        log.info("go2.webrtc.connect.ready")

    async def close(self) -> None:
        """Tear down heartbeat, DataChannel, PeerConnection."""
        if self._closed.is_set():
            return
        self._closed.set()
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._pc is not None:
            await self._pc.close()
        log.info("go2.webrtc.closed")

    def subscribe(self, topic: str) -> None:
        """Tell the robot to start streaming messages for `topic`."""
        self._send({"type": MSG_SUBSCRIBE, "topic": topic})
        log.info("go2.webrtc.subscribe", topic=topic)

    def unsubscribe(self, topic: str) -> None:
        self._send({"type": MSG_UNSUBSCRIBE, "topic": topic})
        log.info("go2.webrtc.unsubscribe", topic=topic)

    def publish_typed(self, type_: str, topic: str, data: Any) -> None:
        """Send a typed message envelope. Returns immediately."""
        self._send({"type": type_, "topic": topic, "data": data})

    async def publish_request(
        self,
        topic: str,
        api_id: int,
        parameter: str = "{}",
        *,
        priority: bool = False,
        wait_for_response: bool = False,
        response_timeout_s: float = 5.0,
    ) -> int | dict[str, Any]:
        """Issue an SDK-style request. Returns the request ID, or the parsed
        response if `wait_for_response=True`.

        Wire format matches legion1581/unitree_ui/src/protocol/data-channel.ts.
        """
        req_id = _new_request_id()
        header: dict[str, Any] = {"identity": {"id": req_id, "api_id": api_id}}
        if priority:
            header["policy"] = {"priority": 1}
        envelope = {
            "type": MSG_REQUEST,
            "topic": topic,
            "data": {"header": header, "parameter": parameter, "binary": []},
        }

        if wait_for_response:
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending_requests[req_id] = _PendingRequest(
                future=future, api_id=api_id, topic=topic,
            )
            self._send(envelope)
            try:
                return await asyncio.wait_for(future, timeout=response_timeout_s)
            finally:
                self._pending_requests.pop(req_id, None)
        self._send(envelope)
        return req_id

    # ── DataChannel callbacks ──────────────────────────────────────

    def _on_channel_open(self) -> None:
        log.info("go2.webrtc.channel.open")
        # Heartbeat starts after validation; flush any subscribes queued
        # while we were waiting.
        for env in self._pre_validation_queue:
            try:
                self._channel.send(json.dumps(env))
            except Exception:
                log.exception("go2.webrtc.flush_failed", envelope=env)
        self._pre_validation_queue.clear()

    def _on_channel_close(self) -> None:
        log.info("go2.webrtc.channel.close")
        # Async close coalesces — best-effort.
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self.close())

    def _on_channel_message(self, raw: Any) -> None:
        """Parse + dispatch one DataChannel frame."""
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        except (ValueError, TypeError) as exc:
            log.warning("go2.webrtc.parse_failed", error=str(exc))
            return

        msg_type = msg.get("type")

        if msg_type == MSG_VALIDATION:
            self._handle_validation(msg)
            return

        # Silently echo heartbeats (the robot sends keep-alive responses)
        if msg_type == MSG_HEARTBEAT:
            return

        # RTC-internal probes: legion1581 echoes RTT probes back unchanged
        if msg_type == MSG_RTC_INNER_REQ:
            info = msg.get("info") or {}
            if isinstance(info, dict) and info.get("req_type") == "rtt_probe_send_from_mechine":
                self._send({"type": MSG_RTC_INNER_REQ, "topic": "", "data": info})
                return

        # Fault stream
        if msg_type in (MSG_ERRORS, MSG_ADD_ERROR, MSG_RM_ERROR):
            for handler in self._error_handlers:
                try:
                    handler(msg_type, msg.get("data"))
                except Exception:
                    log.exception("go2.webrtc.error_handler_failed", msg_type=msg_type)
            return

        # Response correlation
        if msg_type == MSG_RESPONSE:
            data = msg.get("data") or {}
            header = data.get("header") or {}
            identity = header.get("identity") or {}
            req_id = identity.get("id")
            pending = self._pending_requests.get(req_id) if req_id is not None else None
            if pending and not pending.future.done():
                pending.future.set_result(data)
                # response also fans out to topic handlers; intentional

        # Topic data fan-out
        if msg_type in (MSG_MSG, MSG_RESPONSE):
            topic = msg.get("topic") or ""
            data = msg.get("data")
            for handler in self._topic_handlers:
                result = handler(topic, data)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            return

        log.debug("go2.webrtc.unhandled_message", type=msg_type)

    def _handle_validation(self, msg: dict[str, Any]) -> None:
        data = msg.get("data")
        if data == "Validation Ok.":
            log.info("go2.webrtc.validation.ok")
            self._validated.set()
            # Heartbeat starts only after the robot accepts us.
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            return
        if isinstance(data, str):
            response = _validation_response(data)
            log.info("go2.webrtc.validation.challenge", challenge_len=len(data))
            self._send({"type": MSG_VALIDATION, "topic": "", "data": response})

    # ── transport helpers ──────────────────────────────────────────

    def _send(self, envelope: dict[str, Any]) -> None:
        if not self._channel:
            raise RuntimeError("DataChannel not yet created")
        if self._channel.readyState != "open":
            # Queue subscribes / pre-validation traffic, drop everything else.
            if envelope.get("type") in (MSG_SUBSCRIBE, MSG_UNSUBSCRIBE):
                self._pre_validation_queue.append(envelope)
            else:
                log.debug(
                    "go2.webrtc.send.dropped",
                    type=envelope.get("type"),
                    reason="channel_not_open",
                )
            return
        try:
            self._channel.send(json.dumps(envelope))
        except Exception:
            log.exception("go2.webrtc.send_failed", envelope_type=envelope.get("type"))

    async def _exchange_sdp_legacy(self, offer_sdp: str) -> str:
        """POST the SDP offer to `http://<host>:8081/offer`; return the answer.

        Plain JSON request/response — no encryption (firmware ≤ 1.1.14).
        Newer firmware needs the `/con_notify` + `/con_ing_<path>` flow with
        RSA + AES; not implemented in this module yet.
        """
        url = f"http://{self._config.host}:{self._config.port}{self._config.path}"
        payload = {"sdp": offer_sdp, "type": "offer", "id": "C3PO"}
        log.info("go2.webrtc.sdp.send", url=url, sdp_bytes=len(offer_sdp))
        async with httpx.AsyncClient(timeout=self._config.sdp_timeout_s) as http:
            resp = await http.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            answer = resp.json()
            sdp = answer.get("sdp")
            if not isinstance(sdp, str):
                raise RuntimeError(f"unexpected /offer response shape: {answer!r}")
            log.info("go2.webrtc.sdp.recv", answer_bytes=len(sdp))
            return sdp

    def _filter_sdp_to_subnet(self, sdp: str) -> str:
        """Drop `a=candidate:` lines that aren't on the robot's /24.

        aiortc gathers candidates from every local interface; if the Mac has
        a second network (corp LAN, VPN, etc.) it advertises those too. Pairs
        that traverse non-routable interfaces sit in IN_PROGRESS forever and
        starve the one good pair, so we strip them on both sides.
        """
        # Extract the /24 prefix of the robot's host. Works for 192.168.12.1
        # → "192.168.12.". IPv6 / IPv4 edge cases are out of scope today.
        try:
            host_parts = self._config.host.split(".")
            if len(host_parts) != 4:
                return sdp  # don't filter if host isn't a v4 dotted-quad
            subnet_prefix = ".".join(host_parts[:3]) + "."
        except Exception:
            return sdp

        out: list[str] = []
        kept = 0
        dropped = 0
        for line in sdp.splitlines():
            if not line.startswith("a=candidate:"):
                out.append(line)
                continue
            # Layout: a=candidate:<foundation> <component> <transport> <priority> <ip> <port> ...
            parts = line.split()
            if len(parts) < 5:
                out.append(line)
                continue
            ip = parts[4]
            if ip.startswith(subnet_prefix):
                out.append(line)
                kept += 1
            else:
                dropped += 1
        log.info(
            "go2.webrtc.sdp.filter",
            subnet=subnet_prefix,
            candidates_kept=kept,
            candidates_dropped=dropped,
        )
        return "\n".join(out)

    async def _heartbeat_loop(self) -> None:
        period = self._config.heartbeat_period_s
        try:
            while not self._closed.is_set():
                await asyncio.sleep(period)
                self._send(
                    {"type": MSG_HEARTBEAT, "topic": "", "data": _now_heartbeat()},
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("go2.webrtc.heartbeat_loop_crashed")
