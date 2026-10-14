// Minimal WebRTC client for the sim's unitree `teleimager` image servers
// (github.com/unitreerobotics/teleimager), which expose the G1's head + wrist
// cameras. Each camera is its own aiortc server on its own port:
//   60001 head (stereo, 480x1280 side-by-side) · 60002 left wrist · 60003 right wrist
//
// Signaling is plain aiortc: the browser POSTs an SDP offer to
// `https://<host>:<port>/offer` and gets the answer back — no Unitree crypto
// handshake, no DataChannel "vid:on". The server starts pushing H.264 (VP8
// fallback) the moment the offer/answer completes.
//
// Two operational constraints (both from reading the teleimager source):
//   1. TLS is a self-signed cert, so the operator must accept it once per port
//      (open https://<host>:<port> and proceed) or the /offer fetch throws a
//      TypeError.
//   2. The server advertises only host ICE candidates (no STUN/TURN), so the
//      browser must be able to reach <host> directly — same box / same LAN.
//
// That TypeError is opaque by design: an unaccepted certificate, a simulator
// that is simply not running, a wrong host and a refused port all look the
// same from here. It used to be reported as "cert", and the page stated as
// fact that the browser was blocking the camera — so the most common failure,
// the sim being off, sent the operator to go accept certificates. It is
// "unreachable" now, and the copy names both causes.
//
// A FROZEN FRAME IS NOT LIVE. When the server or the network stops, the
// connection goes "disconnected" within seconds but only "failed" after the
// consent timeout, much later; in between the <video> holds its last decoded
// frame. That window is reported as "stale" (and so is a muted track, which
// is the browser saying no packets are arriving), and it returns to "live" on
// "connected" / unmute.

import { SIM_OFFER_TIMEOUT_MS } from "#lib/robot/camera-copy.js";

export type SimCamState = "connecting" | "live" | "stale" | "error" | "closed";

export interface SimCamCallbacks {
  /** Fired once the inbound video track arrives. */
  onStream: (stream: MediaStream) => void;
  /** Connection lifecycle. `detail` is a token or short reason:
   *  "unreachable" (offer fetch threw — cert or sim down), "timeout" (the
   *  offer was never answered — a host that does not answer), "sin video" (the
   *  server answered but no frame came), "conexión interrumpida" / "sin
   *  cuadros" (stale), "conexión perdida" (failed), or "HTTP nnn". */
  onState: (state: SimCamState, detail?: string) => void;
}

export interface SimCamHandle {
  close(): void;
}

// If no frame arrives this fast, retry once on VP8 (the server supports both).
const LIVE_TIMEOUT_MS = 6000;
// Host candidates gather near-instantly on a LAN; don't block forever.
const ICE_TIMEOUT_MS = 1500;

export function connectSimCamera(
  host: string,
  port: number,
  callbacks: SimCamCallbacks,
): SimCamHandle {
  let pc: RTCPeerConnection | null = null;
  let liveTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;
  let triedVp8 = false;

  function teardown() {
    if (liveTimer) {
      clearTimeout(liveTimer);
      liveTimer = null;
    }
    if (pc) {
      pc.ontrack = null;
      pc.onconnectionstatechange = null;
      pc.close();
      pc = null;
    }
  }

  async function attempt(codec: "h264" | "vp8") {
    teardown();
    callbacks.onState("connecting");

    const conn = new RTCPeerConnection({ iceServers: [] }); // host candidates only
    pc = conn;
    conn.addTransceiver("video", { direction: "recvonly" });

    // Only a feed that has shown a picture can be "frozen"; before the first
    // track, a disconnect is just a failed attempt.
    let hadTrack = false;

    conn.ontrack = (e) => {
      if (closed || conn !== pc || e.track.kind !== "video") return;
      hadTrack = true;
      callbacks.onStream(e.streams[0] ?? new MediaStream([e.track]));
      callbacks.onState("live");
      if (liveTimer) {
        clearTimeout(liveTimer);
        liveTimer = null;
      }
      e.track.onmute = () => {
        if (closed || conn !== pc) return;
        callbacks.onState("stale", "sin cuadros");
      };
      e.track.onunmute = () => {
        if (closed || conn !== pc) return;
        if (conn.connectionState === "connected") callbacks.onState("live");
      };
    };
    conn.onconnectionstatechange = () => {
      if (closed || conn !== pc) return;
      const st = conn.connectionState;
      if (st === "failed") callbacks.onState("error", "conexión perdida");
      else if (st === "disconnected" && hadTrack)
        callbacks.onState("stale", "conexión interrumpida");
      else if (st === "connected" && hadTrack) callbacks.onState("live");
    };

    try {
      await conn.setLocalDescription(await conn.createOffer());
      await waitIceComplete(conn);
      if (closed || conn !== pc) return;

      // Bounded. Without it a host that silently drops packets (the sim on
      // another VLAN, a wrong PUBLIC_SIM_CAM_HOST) held the panel on
      // "Conectando…" for the OS's TCP timeout — tens of seconds, sometimes
      // minutes — with no word on what was wrong.
      const res = await fetch(`https://${host}:${port}/offer`, {
        signal: AbortSignal.timeout(SIM_OFFER_TIMEOUT_MS),
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          sdp: conn.localDescription!.sdp,
          type: conn.localDescription!.type,
          codec,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const answer = (await res.json()) as RTCSessionDescriptionInit;
      if (closed || conn !== pc) return;
      await conn.setRemoteDescription(answer);

      liveTimer = setTimeout(() => {
        if (closed || conn !== pc) return;
        if (codec === "h264" && !triedVp8) {
          triedVp8 = true;
          attempt("vp8");
        } else {
          callbacks.onState("error", "sin video");
        }
      }, LIVE_TIMEOUT_MS);
    } catch (err) {
      if (closed || conn !== pc) return;
      // A self-signed-cert reject and an unreachable host both surface as a
      // fetch TypeError (opaque by design). Report what we know — that the
      // server could not be reached — and let the UI name both causes.
      const detail =
        err instanceof DOMException &&
        (err.name === "TimeoutError" || err.name === "AbortError")
          ? "timeout"
          : err instanceof TypeError
            ? "unreachable"
            : (err as Error).message;
      callbacks.onState("error", detail);
    }
  }

  attempt("h264");

  return {
    close() {
      if (closed) return;
      closed = true;
      teardown();
      callbacks.onState("closed");
    },
  };
}

function waitIceComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      pc.removeEventListener("icegatheringstatechange", onChange);
      resolve();
    };
    const onChange = () => {
      if (pc.iceGatheringState === "complete") finish();
    };
    pc.addEventListener("icegatheringstatechange", onChange);
    setTimeout(finish, ICE_TIMEOUT_MS);
  });
}
