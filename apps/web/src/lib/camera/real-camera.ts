// WebSocket client for the real G1's camera relay
// (`apps/bridge/src/bridge/camera_relay.py`) -- a passive ZeroMQ subscriber
// to teleimager's existing JPEG feed, re-published as one binary WebSocket
// message per frame. This is NOT the sim's per-camera aiortc/WebRTC setup
// (`webrtc/sim-camera.ts`): the real G1 has one monocular camera and a
// different transport entirely (see that module's docstring and
// docs/ROBOT-PERIPHERALS.md §2.4).
//
// Deployment gap, stated plainly rather than glossed over: the relay binds
// loopback on the Jetson by default (same "no auth of its own, tunnel in"
// posture as the bridge's own MCP port, see that module's docstring), so
// this only works today when the browser can reach it directly -- same LAN,
// or an SSH tunnel forwarding CAMERA_RELAY_PORT, exactly how bench dev
// against the bridge already works. The production topology (`apps/web` on
// Vercel -> `apps/back` on the school LAN -> Jetson) would need `apps/back`
// to proxy this WebSocket the way it proxies MCP tool calls -- not built
// yet, and a separate piece of work (apps/back has no WebSocket routes at
// all today, see SPEC.md §9's "planned but never built" note on that).

export type RealCamState = "connecting" | "live" | "error" | "closed";

export interface RealCamCallbacks {
  /** Fired for every decoded frame -- an object URL suitable for `<img
   *  src>`. This module owns the URL's lifetime (revokes the previous one
   *  itself); callers should not revoke it. */
  onFrame: (objectUrl: string) => void;
  onState: (state: RealCamState, detail?: string) => void;
}

export interface RealCamHandle {
  close(): void;
}

// No frame at all this long after the socket opens means something's wrong
// upstream (teleimager down, relay can't reach it) even though the
// WebSocket itself connected fine.
const FIRST_FRAME_TIMEOUT_MS = 6000;

export function connectRealCamera(
  url: string,
  callbacks: RealCamCallbacks,
): RealCamHandle {
  let closed = false;
  let lastObjectUrl: string | null = null;
  let firstFrameTimer: ReturnType<typeof setTimeout> | null = null;

  callbacks.onState("connecting");
  const ws = new WebSocket(url);
  ws.binaryType = "blob";

  function revokeLast() {
    if (lastObjectUrl) {
      URL.revokeObjectURL(lastObjectUrl);
      lastObjectUrl = null;
    }
  }

  function clearFirstFrameTimer() {
    if (firstFrameTimer) {
      clearTimeout(firstFrameTimer);
      firstFrameTimer = null;
    }
  }

  ws.onopen = () => {
    if (closed) return;
    firstFrameTimer = setTimeout(() => {
      if (closed) return;
      callbacks.onState("error", "sin video");
    }, FIRST_FRAME_TIMEOUT_MS);
  };

  ws.onmessage = (ev) => {
    if (closed || !(ev.data instanceof Blob)) return;
    clearFirstFrameTimer();
    const nextUrl = URL.createObjectURL(ev.data);
    revokeLast();
    lastObjectUrl = nextUrl;
    callbacks.onFrame(nextUrl);
    callbacks.onState("live");
  };

  ws.onerror = () => {
    if (closed) return;
    callbacks.onState("error", "conexión perdida");
  };

  ws.onclose = () => {
    if (closed) return;
    callbacks.onState("error", "conexión cerrada");
  };

  return {
    close() {
      if (closed) return;
      closed = true;
      clearFirstFrameTimer();
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      ws.close();
      revokeLast();
      callbacks.onState("closed");
    },
  };
}
