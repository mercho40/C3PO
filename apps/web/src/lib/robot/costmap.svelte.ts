/**
 * Nav2's global costmap, polled as a PNG and georeferenced for the map canvas.
 *
 *   back  GET /map/costmap.png  ->  image/png + X-C3PO-* placement headers
 *
 * Fetched with `fetch` rather than pointed at with an `<img src>`, because the
 * placement metadata rides on RESPONSE HEADERS: without the origin, resolution
 * and grid size there is no way to put the image in the same coordinate frame
 * as the robot marker, and it would silently land at the canvas origin looking
 * approximately right.
 *
 * WHAT COUNTS AS "NO MAP" IS A DELIBERATE DISTINCTION.
 *
 * 503 from the proxy is not an error — it is the honest answer whenever no
 * nav2 stage is running, which is most of the time. It carries a `hint` saying
 * which command would produce one, and that reaches the operator verbatim. A
 * transport failure is a different state (`unreachable`), and a map with a
 * stale age is a third. Collapsing those into one "no map" would leave the
 * operator unable to tell "nothing is mapping" from "the console is broken" —
 * the same absent-is-not-empty rule the world model is built on, applied to a
 * picture.
 *
 * AN OLD MAP IS KEPT, AND SAID TO BE OLD. Once a map has been drawn, a later
 * failure used to leave it on screen with its last, frozen "0.4 s" label —
 * the reason hidden in a hover tooltip — because the label checked "is there
 * a url?" before anything else. Now every poll records its own outcome in
 * `status`; the image is retained, but `current` is false and `ageS` keeps
 * counting from the last good answer, so the page can say "mapa no disponible
 * · último hace 42 s" next to it. Showing an old map is fine; showing it as
 * current is not.
 *
 * IT BACKS OFF WHILE THERE IS NOTHING TO SHOW. A 503 is the normal answer
 * whenever no nav2 stage runs — most of the time — and polling it at 1 Hz
 * all day was ~3600 proxied tool calls an hour to learn the same thing.
 * After a few consecutive non-live answers the poll stretches out (capped at
 * BACKOFF_MAX_MS, short enough that an operator who just started nav2 sees
 * the map within seconds); the first live answer restores 1 Hz.
 *
 * OBJECT URLs ARE REVOKED. At 1 Hz an unrevoked blob URL leaks a costmap per
 * second for as long as the page is open; the browser will not collect them
 * while the document holds a reference.
 */

import { PUBLIC_API_URL } from "$env/static/public";
import { LINK_DETAIL, linkFromError } from "./link-copy";
import { Poller, type PollResult } from "./poller.svelte";

/** Where the grid sits in the world, in metres, from the X-C3PO-* headers. */
export type CostmapPlacement = {
  /** Frame the origin is expressed in. `odom` today — there is no map frame. */
  frame: string;
  /** World position of the grid's BOTTOM-LEFT corner. */
  originXM: number;
  originYM: number;
  /** Full extent of the grid. */
  widthM: number;
  heightM: number;
  /** Metres per cell, kept for the scale readout. */
  resolutionM: number;
};

/**
 * What the LAST poll found — not what is on screen.
 *  - `loading`      no answer yet; nothing is known
 *  - `live`         a placed map arrived on this poll
 *  - `noCostmap`    503: the bridge is up and nothing is publishing a costmap
 *  - `noPlacement`  a map arrived without the headers needed to place it
 *  - `unreachable`  back, or the bridge behind it, did not answer
 */
export type CostmapStatus =
  | "loading"
  | "live"
  | "noCostmap"
  | "noPlacement"
  | "unreachable";

const DEFAULT_POLL_MS = 1000;
/** Non-live answers tolerated at full rate before backing off. */
const BACKOFF_AFTER = 3;
/** The longest a "no map" may go unchecked. */
const BACKOFF_MAX_MS = 5000;

function num(headers: Headers, name: string): number | null {
  const raw = headers.get(name);
  if (raw === null || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/**
 * Pauses while the tab is hidden — a background map tab has nobody reading
 * it — and a 401 goes to the login page through `Poller`, never into a
 * "map unavailable (401)" that blames the robot for an expired session.
 */
export class Costmap extends Poller {
  /** Object URL of the last placed PNG — retained across failures. */
  url = $state<string | null>(null);
  placement = $state<CostmapPlacement | null>(null);
  /** The bridge's X-C3PO-Age-S at the last good answer. */
  headerAgeS = $state<number | null>(null);
  /** The bridge's own X-C3PO-Stale verdict on the last map it sent. */
  bridgeStale = $state(false);
  /** Wall-clock ms the retained map arrived. */
  lastGoodAt = $state<number | null>(null);
  /** Wall-clock ms of the last poll, of any outcome — keeps ages counting. */
  checkedAt = $state<number | null>(null);
  status = $state<CostmapStatus>("loading");
  /** Why there is no CURRENT map, in Spanish. Null when current. */
  reason = $state<string | null>(null);
  /** The bridge's own hint (English, verbatim), e.g. the command to run. */
  hint = $state<string | null>(null);
  /**
   * A placed map has arrived at least once. Flips once and never back, so a
   * page can derive a default from it (follow the robot once there is a map)
   * without that default being re-asserted on every poll.
   */
  everLoaded = $state(false);

  /** Consecutive polls that ended without a live map. */
  #quiet = 0;
  /** Wall-clock ms before which interval ticks are skipped (backoff). */
  #skipUntil = 0;

  constructor() {
    super({ timeoutMs: 3000, pauseWhenHidden: true });
  }

  /**
   * The interval keeps its 1 s cadence; while backing off, ticks inside the
   * window are skipped. (Poller owns the timer, so the backoff is applied
   * here rather than by re-arming it.)
   */
  async tick(): Promise<void> {
    if (Date.now() < this.#skipUntil) return;
    await super.tick();
  }

  /** Record a poll's outcome for the backoff: live resets it. */
  #settle(live: boolean): void {
    if (live) {
      this.#quiet = 0;
      this.#skipUntil = 0;
      return;
    }
    this.#quiet += 1;
    const over = this.#quiet - BACKOFF_AFTER;
    this.#skipUntil =
      over > 0
        ? Date.now() +
          Math.min(BACKOFF_MAX_MS, DEFAULT_POLL_MS * 2 ** (over - 1))
        : 0;
  }

  /** The map on screen came from the most recent poll. */
  get current(): boolean {
    return this.status === "live";
  }

  /** The drawn map should read as old: not current, or the bridge says so. */
  get stale(): boolean {
    return this.url !== null && (!this.current || this.bridgeStale);
  }

  /** Kept for callers of the old shape: the fetch itself failed. */
  get unreachable(): boolean {
    return this.status === "unreachable";
  }

  /**
   * Age of the map on screen: the bridge's age at arrival plus the time since,
   * measured up to the last poll (so it advances only while we are checking).
   */
  get ageS(): number | null {
    if (this.lastGoodAt === null) return null;
    const since =
      Math.max(0, (this.checkedAt ?? this.lastGoodAt) - this.lastGoodAt) / 1000;
    return (this.headerAgeS ?? 0) + since;
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    const result = await this.#poll(signal);
    this.#settle(this.status === "live");
    return result;
  }

  async #poll(signal: AbortSignal): Promise<PollResult> {
    let res: Response;
    try {
      res = await fetch(`${PUBLIC_API_URL}/map/costmap.png`, {
        credentials: "include",
        signal,
      });
    } catch {
      this.#fail(
        signal.aborted
          ? "El servidor tardó demasiado en responder."
          : LINK_DETAIL.noApi,
      );
      return "error";
    } finally {
      this.checkedAt = Date.now();
    }

    if (res.status === 401) return "unauthorized";

    if (res.status === 503) {
      // Expected whenever no nav2 stage is up. The last image (if any) stays
      // on screen, flagged as old by `current` going false.
      const body = (await res.json().catch(() => null)) as {
        hint?: string;
        error?: string;
      } | null;
      this.status = "noCostmap";
      this.reason = "No hay costmap publicado.";
      this.hint = body?.hint ?? body?.error ?? null;
      return "ok";
    }
    if (!res.ok) {
      // Read back's {error} so "the bridge is down" (502 bridge_unavailable)
      // is told apart from a console that cannot reach back at all.
      const body = await res.json().catch(() => null);
      this.#fail(LINK_DETAIL[linkFromError(res.status, body)]);
      return "error";
    }

    const originXM = num(res.headers, "X-C3PO-Origin-X-M");
    const originYM = num(res.headers, "X-C3PO-Origin-Y-M");
    const resolutionM = num(res.headers, "X-C3PO-Resolution-M");
    const width = num(res.headers, "X-C3PO-Width");
    const height = num(res.headers, "X-C3PO-Height");

    if (
      originXM === null ||
      originYM === null ||
      resolutionM === null ||
      width === null ||
      height === null
    ) {
      // Without placement the image cannot be positioned, and drawing it
      // anyway would put a map of somewhere else under the robot marker. A
      // map IS being published, though — so not "sin mapa".
      this.status = "noPlacement";
      this.reason =
        "El mapa llegó sin metadatos de ubicación: no se puede ubicar bajo el robot.";
      this.hint = null;
      return "ok";
    }

    // The body is read inside its own try: a connection that drops mid-PNG
    // used to throw past this method into Poller's catch, which only counts
    // a failure — leaving `status` "live" (and the map labelled current)
    // over the previous picture.
    let blob: Blob;
    try {
      blob = await res.blob();
    } catch {
      this.#fail(
        signal.aborted
          ? "El servidor tardó demasiado en mandar el mapa."
          : "El mapa llegó incompleto: se cortó la conexión a mitad de la imagen.",
      );
      return "error";
    }
    const blobUrl = URL.createObjectURL(blob);
    const previous = this.url;
    this.url = blobUrl;
    if (previous) URL.revokeObjectURL(previous);

    this.placement = {
      frame: res.headers.get("X-C3PO-Frame") ?? "odom",
      originXM,
      originYM,
      widthM: width * resolutionM,
      heightM: height * resolutionM,
      resolutionM,
    };
    this.headerAgeS = num(res.headers, "X-C3PO-Age-S");
    this.bridgeStale = res.headers.get("X-C3PO-Stale") === "true";
    this.lastGoodAt = Date.now();
    this.checkedAt = this.lastGoodAt;
    this.status = "live";
    this.everLoaded = true;
    this.reason = null;
    this.hint = null;
    return "ok";
  }

  #fail(reason: string): void {
    this.status = "unreachable";
    this.reason = reason;
    this.hint = null;
  }

  start(intervalMs: number = DEFAULT_POLL_MS): void {
    // The costmap publishes at 1 Hz; polling faster only re-fetches the same
    // grid.
    super.start(intervalMs);
  }

  stop(): void {
    super.stop();
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
  }
}
