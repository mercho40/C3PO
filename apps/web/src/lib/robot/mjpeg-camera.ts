// The REAL G1's camera. Sibling of `$lib/webrtc/sim-camera.ts`, and the two are
// different on purpose — the robot and the simulator do not serve video the same
// way, and pretending they do is what left `/live-camera` dead on hardware.
//
// The source is `apps/perception`'s vision container: it already owns the
// D435i (`/dev/videoN` has exactly one owner), so it is also the only process
// that can show the operator a picture. It serves:
//
//   GET /stream.mjpg   multipart/x-mixed-replace   an <img src> renders it
//   GET /status        JSON                        whether that picture is live
//
// WHY THE STATUS POLL EXISTS
// --------------------------
// An <img> pointed at an MJPEG stream keeps displaying the last frame it got,
// forever, with no event. A camera that died two minutes ago looks exactly like
// one that is working — which is the failure this console already refuses to
// commit (the sim page deleted its fake HUD room for the same reason). So the
// picture comes from the <img> and the TRUTH comes from /status: it carries the
// age of the newest frame, and the server closes the stream once that age
// crosses its own threshold.
//
// REACHABILITY
// ------------
// The stream binds the Jetson's loopback by default, so `PUBLIC_ROBOT_CAM_URL`
// normally points at a local port forwarded over the same SSH tunnel that
// carries the bridge:
//   ssh -f -N -L 8001:127.0.0.1:8001 -L 8081:127.0.0.1:8081 c3po
// That is also why this is plain HTTP with no certificate dance: the transport
// is already an encrypted tunnel, and a self-signed cert per port is the thing
// that makes the sim's cameras annoying to bring up.

export type RobotCamState = "connecting" | "live" | "stale" | "error" | "closed";

export interface RobotCamStatus {
  live: boolean;
  frame_age_s: number | null;
  frames: number;
  clients: number;
  width: number | null;
  height: number | null;
  stream_width: number | null;
  stream_height: number | null;
  stale_after_s: number;
}

export interface RobotCamCallbacks {
  /** Lifecycle. `detail` is a short human reason, already in the UI's language. */
  onState: (state: RobotCamState, detail?: string) => void;
  /** The last `/status` payload, or null when it could not be read. */
  onStatus: (status: RobotCamStatus | null) => void;
  /** A new <img> src to mount. Changes only when the stream must be REopened. */
  onStreamUrl: (url: string) => void;
}

export interface RobotCamHandle {
  close(): void;
  /** Force a fresh stream connection (new URL) plus an immediate status read. */
  reconnect(): void;
}

// The vision container's own staleness threshold is 1 s and it closes the
// connection at that point; polling faster than that only adds requests, and
// polling much slower would let the badge lag the picture.
const POLL_MS = 1000;

/** `http://host:8081/` + `stream.mjpg`, tolerating a trailing slash or none. */
export function endpoint(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, "")}/${path}`;
}

export function connectRobotCamera(
  baseUrl: string,
  callbacks: RobotCamCallbacks,
): RobotCamHandle {
  let timer: ReturnType<typeof setInterval> | null = null;
  let closed = false;
  let lastState: RobotCamState | null = null;
  // Bumped on every reconnect. The browser will happily reuse a dead MJPEG
  // response for the same URL, so a retry that does not change the src is a
  // retry that does nothing.
  let attempt = 0;

  function setState(state: RobotCamState, detail?: string) {
    if (closed || state === lastState) return;
    lastState = state;
    callbacks.onState(state, detail);
  }

  async function poll() {
    if (closed) return;
    try {
      const res = await fetch(endpoint(baseUrl, "status"), {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const status = (await res.json()) as RobotCamStatus;
      if (closed) return;
      callbacks.onStatus(status);
      if (status.live) {
        setState("live");
      } else if (status.frames > 0) {
        // Frames arrived once and stopped: the detector is up but its ticks are
        // failing, which is a different fact from "nothing is running" and the
        // operator should be told which one they have.
        setState(
          "stale",
          status.frame_age_s === null
            ? "sin cuadros nuevos"
            : `sin cuadros hace ${status.frame_age_s.toFixed(1)} s`,
        );
      } else {
        setState("stale", "la cámara todavía no entregó ningún cuadro");
      }
    } catch (err) {
      if (closed) return;
      callbacks.onStatus(null);
      // Unreachable is the common case here and it has one likely cause worth
      // naming: the SSH tunnel is not up, or perception is not running.
      setState("error", err instanceof TypeError ? "tunnel" : (err as Error).message);
    }
  }

  function open() {
    attempt += 1;
    setState("connecting");
    callbacks.onStreamUrl(`${endpoint(baseUrl, "stream.mjpg")}?c=${attempt}`);
    void poll();
  }

  open();
  timer = setInterval(() => void poll(), POLL_MS);

  return {
    close() {
      if (closed) return;
      closed = true;
      if (timer) clearInterval(timer);
      timer = null;
      lastState = null;
      callbacks.onState("closed");
    },
    reconnect() {
      if (closed) return;
      lastState = null;
      open();
    },
  };
}
