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
// The onboard bridge exposes this directly at g1-orin.local:8001. It is plain
// HTTP, so it works from the local HTTP dev console; an HTTPS-hosted console
// needs an HTTPS/WSS gateway before browsers will accept the mixed content.

export type RobotCamState =
  | "connecting"
  | "live"
  | "stale"
  | "error"
  | "closed";

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

// Half the vision container's own 1 s staleness threshold, not equal to it.
//
// Equal was a real bug. The server ends the response after `stale_after_s`
// without a frame — that is its only in-band way to say "no longer live" — but
// `/status.live` is an age comparison sampled at one instant. With a 1 s poll
// against a 1 s threshold, a gap of 1.0-2.0 s closes the connection while
// every poll happens to land on a fresh frame either side of it. Nothing ever
// observes `live: false`, so nothing reopens, and the <img> holds its last
// frame for the rest of the session at full brightness with a green badge.
//
// Sampling twice as fast shrinks that window; the counter below closes it.
const POLL_MS = 500;

// How many consecutive polls may report no new frames before the stream is
// assumed dead. Two polls at 500 ms is the server's own 1 s threshold — the
// point at which it has definitely closed the response on us — so this catches
// exactly the gaps the age check misses, and no sooner.
const STALLED_POLLS_BEFORE_REOPEN = 2;

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
  // Whether the server has likely closed the stream. Set whenever it reports
  // not-live, because that is exactly the condition under which it ends the
  // response; cleared by a successful reopen.
  let streamDead = false;
  let lastDetail: string | undefined;

  //: The server's own monotonic frame counter, as of the previous poll. The
  //: age check cannot see a gap that both polls straddle; a counter that has
  //: not moved between two polls can.
  let lastFrames = -1;
  let stalledPolls = 0;

  function setState(state: RobotCamState, detail?: string) {
    // Compares the detail too. Deduping on `state` alone meant a repeated
    // "stale" dropped its message, so the "sin cuadros hace N s" text froze at
    // whatever N was on the first stale poll and never counted up — the one
    // number telling the operator whether it is getting worse.
    if (closed || (state === lastState && detail === lastDetail)) return;
    lastState = state;
    lastDetail = detail;
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
        // Reopen if the stream is believed dead. The server ENDS the response
        // after `stale_after_s` without a frame — that is its only in-band way
        // to say "no longer live" — so recovery is not automatic: the <img>
        // holds its last frame forever against a connection the server already
        // closed. `/status` going live again is the signal, and the URL must
        // change or the browser reuses the dead response.
        //
        // Without this, one momentary stall left the picture frozen for the
        // rest of the session while this endpoint cheerfully reported "live".
        // A gap the age check cannot see: `live` is true at both ends of it,
        // but the server closed our response somewhere in the middle and the
        // <img> is now showing a still photograph.
        if (status.frames === lastFrames) {
          stalledPolls += 1;
          if (stalledPolls >= STALLED_POLLS_BEFORE_REOPEN) {
            stalledPolls = 0;
            lastFrames = status.frames;
            open();
            return;
          }
        } else {
          stalledPolls = 0;
        }
        lastFrames = status.frames;

        if (streamDead) {
          streamDead = false;
          open();
          return;
        }
        setState("live");
      } else if (status.frames > 0) {
        // Frames arrived once and stopped: the detector is up but its ticks are
        // failing, which is a different fact from "nothing is running" and the
        // operator should be told which one they have.
        streamDead = true;
        setState(
          "stale",
          status.frame_age_s === null
            ? "sin cuadros nuevos"
            : `sin cuadros hace ${status.frame_age_s.toFixed(1)} s`,
        );
      } else {
        streamDead = true;
        setState("stale", "la cámara todavía no entregó ningún cuadro");
      }
    } catch (err) {
      if (closed) return;
      // Unreachable means the stream is gone too — whatever comes back later
      // needs a fresh connection, not the one that died with the network path.
      streamDead = true;
      callbacks.onStatus(null);
      // Unreachable is the common case here and it has one likely cause worth
      // naming: the robot is off-LAN/unreachable, or perception is not running.
      setState(
        "error",
        err instanceof TypeError ? "network" : (err as Error).message,
      );
    }
  }

  function open() {
    attempt += 1;
    streamDead = false;
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
