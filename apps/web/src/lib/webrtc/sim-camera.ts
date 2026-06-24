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
//      TypeError — surfaced here as state "error" with detail "cert".
//   2. The server advertises only host ICE candidates (no STUN/TURN), so the
//      browser must be able to reach <host> directly — same box / same LAN.

export type SimCamState = "connecting" | "live" | "error" | "closed";

export interface SimCamCallbacks {
  /** Fired once the inbound video track arrives. */
  onStream: (stream: MediaStream) => void;
  /** Connection lifecycle. `detail` is "cert" for a likely cert/reachability
   *  failure, otherwise a short reason. */
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

    conn.ontrack = (e) => {
      if (closed || conn !== pc || e.track.kind !== "video") return;
      callbacks.onStream(e.streams[0] ?? new MediaStream([e.track]));
      callbacks.onState("live");
      if (liveTimer) {
        clearTimeout(liveTimer);
        liveTimer = null;
      }
    };
    conn.onconnectionstatechange = () => {
      if (closed || conn !== pc) return;
      if (conn.connectionState === "failed") callbacks.onState("error", "conexión perdida");
    };

    try {
      await conn.setLocalDescription(await conn.createOffer());
      await waitIceComplete(conn);
      if (closed || conn !== pc) return;

      const res = await fetch(`https://${host}:${port}/offer`, {
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
      // A self-signed-cert reject or unreachable host both surface as a fetch
      // TypeError (opaque by design); flag it as "cert" so the UI can guide.
      const detail = err instanceof TypeError ? "cert" : (err as Error).message;
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
