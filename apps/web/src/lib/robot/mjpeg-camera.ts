// The REAL G1's camera. Sibling of `#lib/webrtc/sim-camera.ts`, and the two are
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
// `PUBLIC_ROBOT_CAM_URL` points at apps/back (`http://localhost:3000/camera`),
// which relays the bridge's `/camera/*` behind Better Auth — the same path
// `/telemetry/*` and the costmap take. The browser never talks to the bridge's
// port, which also serves the unauthenticated `/mcp`. Hence
// `credentials: "include"` below: the session cookie is what gets the picture.
// apps/back reaches the bridge at its own BRIDGE_URL.
//
// The bridge relays whichever of TWO servers currently owns the one physical
// camera — the vendor's videohub feed (1920x1080) or apps/perception's vision
// container (640x480) — and `/status` names it in `source`, reports both in
// `sources`, and carries a `hint` when neither is live. Those fields are what
// turn "the feed is dark" into "somebody took the device", so they are passed
// through, never dropped.
//
// FAILURE DETAILS ARE TOKENS, translated by `camera-copy.ts`:
//   "network"      the /status fetch threw and the host did not answer at all
//   "cors"         the host answered, but the browser hid the answer — this
//                  console's origin is not allowed: apps/back's WEB_URL, or
//                  BRIDGE_CORS_ORIGINS when pointed at the bridge directly
//   "timeout"      no answer within STATUS_TIMEOUT_MS
//   "HTTP 503"     a non-OK answer, optionally followed by ": <the bridge's own
//                  words>" (its `hint`, else its `error`), verbatim

export type RobotCamState =
  "connecting" | "live" | "stale" | "error" | "closed";

/** Which server is behind the bridge's relay. `none` = neither is live. */
export type RobotCamSource = "videohub" | "vision" | "none";

/** One of the two upstream servers, as the bridge last saw it. */
export interface RobotCamSourceState {
  live: boolean;
  /** The bridge's own sentence on why this source is dark. English, verbatim. */
  hint?: string | null;
}

export interface RobotCamStatus {
  live: boolean;
  frame_age_s: number | null;
  frames: number;
  /** Present on the vision container's own /status; the bridge omits it. */
  clients?: number;
  width: number | null;
  height: number | null;
  stream_width: number | null;
  stream_height: number | null;
  stale_after_s: number;
  /** Bridge relay only: which server is serving the picture. */
  source?: RobotCamSource;
  /** Bridge relay only: both servers, always, so a dark feed names who was asked. */
  sources?: {
    videohub?: RobotCamSourceState;
    vision?: RobotCamSourceState;
  };
  /** Bridge relay only, when neither source is live: the ordinary cause. */
  hint?: string | null;
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

// A status read that has not answered in this long is a failure, not a wait.
// Without it an mDNS name that resolves to nobody held each fetch open for the
// OS's TCP timeout (tens of seconds) while the 500 ms interval kept starting
// new ones behind it.
const STATUS_TIMEOUT_MS = 2500;

// BACKOFF, ONLY WHILE THERE IS NO PICTURE.
//
// The 500 ms cadence above exists for one job: catching a LIVE stream that
// silently stalled. Against a camera that is not there — a permanent 503 in
// the sim ("real-hardware only"), a bridge that is down, a source that never
// produced a frame — it was 20+ requests every 10 s, all day, to re-learn
// the same answer (and on the robot each one is a relay round-trip on the
// Jetson). So after BACKOFF_AFTER_POLLS consecutive polls without a live
// answer, the interval keeps ticking but polls are skipped, doubling up to
// BACKOFF_MAX_MS. The first live answer — or an explicit reconnect —
// restores full speed. The cap is short on purpose: an operator who just
// started videohub should see the picture within seconds.
const BACKOFF_AFTER_POLLS = 4;
const BACKOFF_MAX_MS = 5000;

/**
 * Delay before the next status poll after `quiet` consecutive non-live
 * answers. 0 = keep the normal 500 ms cadence.
 */
export function statusBackoffMs(quiet: number): number {
  const over = quiet - BACKOFF_AFTER_POLLS;
  return over > 0 ? Math.min(BACKOFF_MAX_MS, POLL_MS * 2 ** over) : 0;
}

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

  // Consecutive polls without a live answer, and the backoff it buys.
  let quiet = 0;
  let skipUntil = 0;

  function settle(live: boolean) {
    quiet = live ? 0 : quiet + 1;
    const delay = statusBackoffMs(quiet);
    skipUntil = delay > 0 ? Date.now() + delay : 0;
  }

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

  // Never two status reads in flight: a slow answer is a stale answer, and
  // stacking them up behind a dead host only resolves them out of order.
  let inFlight = false;

  async function poll(force = false) {
    if (closed || inFlight) return;
    if (!force && Date.now() < skipUntil) return;
    inFlight = true;
    try {
      await pollOnce();
    } finally {
      inFlight = false;
    }
  }

  async function pollOnce() {
    try {
      const res = await fetch(endpoint(baseUrl, "status"), {
        cache: "no-store",
        // The feed is served by apps/back behind Better Auth; without the
        // session cookie every poll is a 401 and the panel reads "error".
        credentials: "include",
        signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
      });
      if (!res.ok) throw new HttpError(res.status, await bridgeWords(res));
      const status = (await res.json()) as RobotCamStatus;
      if (closed) return;
      callbacks.onStatus(status);
      settle(status.live);
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
      settle(false);
      // Unreachable means the stream is gone too — whatever comes back later
      // needs a fresh connection, not the one that died with the network path.
      streamDead = true;
      callbacks.onStatus(null);
      // Unreachable is the common case here, but a TypeError hides two very
      // different causes behind one opaque message: nobody answered (off-LAN,
      // mDNS, `c3po up`), or somebody answered and the browser refused to
      // show us the reply because this console's origin is not in the
      // bridge's BRIDGE_CORS_ORIGINS. They are different layers to debug, so
      // a no-cors probe tells them apart: it succeeds (opaquely) exactly when
      // the host is up.
      const detail =
        err instanceof HttpError
          ? err.detail
          : isTimeout(err)
            ? "timeout"
            : err instanceof TypeError
              ? await classifyNetworkFailure(baseUrl)
              : (err as Error).message;
      if (closed) return;
      setState("error", detail);
    }
  }

  function open() {
    attempt += 1;
    streamDead = false;
    setState("connecting");
    callbacks.onStreamUrl(`${endpoint(baseUrl, "stream.mjpg")}?c=${attempt}`);
    void poll(true);
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
      // An explicit reconnect is someone looking: full speed again.
      quiet = 0;
      skipUntil = 0;
      open();
    },
  };
}

/** A non-OK /status answer, carrying the detail token for it. */
class HttpError extends Error {
  readonly detail: string;
  constructor(status: number, words: string | null) {
    super(`HTTP ${status}`);
    // "HTTP 503" alone tells nobody anything. The bridge's 503 says WHY — e.g.
    // that this is the sim and the camera is real-hardware only — so its own
    // sentence rides along after the code, verbatim.
    this.detail = words ? `HTTP ${status}: ${words}` : `HTTP ${status}`;
  }
}

/** The bridge's `hint` (else `error`) from a JSON error body, if there is one. */
async function bridgeWords(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { hint?: unknown; error?: unknown };
    const words = typeof body.hint === "string" ? body.hint : body.error;
    return typeof words === "string" && words.trim() ? words.trim() : null;
  } catch {
    return null;
  }
}

function isTimeout(err: unknown): boolean {
  return (
    err instanceof DOMException &&
    (err.name === "TimeoutError" || err.name === "AbortError")
  );
}

/** "cors" when the host answers an opaque request, else "network". */
async function classifyNetworkFailure(baseUrl: string): Promise<string> {
  try {
    await fetch(endpoint(baseUrl, "status"), {
      mode: "no-cors",
      cache: "no-store",
      signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
    });
    return "cors";
  } catch {
    return "network";
  }
}
