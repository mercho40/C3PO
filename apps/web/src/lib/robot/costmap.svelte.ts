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
 * transport failure is a different state (`error`), and an empty-looking map
 * with a stale age is a third. Collapsing those into one "no map" would leave
 * the operator unable to tell "nothing is mapping" from "the console is
 * broken" — the same absent-is-not-empty rule the world model is built on,
 * applied to a picture.
 *
 * OBJECT URLs ARE REVOKED. At 1 Hz an unrevoked blob URL leaks a costmap per
 * second for as long as the page is open; the browser will not collect them
 * while the document holds a reference.
 */

import { PUBLIC_API_URL } from "$env/static/public";

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

const DEFAULT_POLL_MS = 1000;

function num(headers: Headers, name: string): number | null {
  const raw = headers.get(name);
  if (raw === null || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

export class Costmap {
  /** Object URL for the current PNG, or null when there is nothing to draw. */
  url = $state<string | null>(null);
  placement = $state<CostmapPlacement | null>(null);
  /** Seconds since the robot produced this grid, per the bridge. */
  ageS = $state<number | null>(null);
  /** The bridge's own verdict, not a threshold recomputed here. */
  stale = $state(false);
  /** Why there is no map, in words the operator can act on. Null when fine. */
  reason = $state<string | null>(null);
  /** Distinguishes "no costmap is being produced" from "the fetch failed". */
  unreachable = $state(false);

  #timer: ReturnType<typeof setInterval> | null = null;
  #inFlight = false;

  async #tick(): Promise<void> {
    // A slow response must not stack polls on top of each other; at 1 Hz that
    // would quietly turn into a queue of pending costmaps.
    if (this.#inFlight) return;
    this.#inFlight = true;
    try {
      const res = await fetch(`${PUBLIC_API_URL}/map/costmap.png`, {
        credentials: "include",
      });

      if (res.status === 503) {
        // Expected whenever no nav2 stage is up. Keep the last image on screen
        // rather than blanking it — but say why nothing is arriving.
        const body = (await res.json().catch(() => null)) as {
          hint?: string;
        } | null;
        this.reason = body?.hint ?? "no costmap is being published";
        this.unreachable = false;
        return;
      }
      if (!res.ok) {
        this.reason = `map unavailable (${res.status})`;
        this.unreachable = true;
        return;
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
        // anyway would put a map of somewhere else under the robot marker.
        this.reason = "map arrived without placement metadata";
        this.unreachable = false;
        return;
      }

      const blobUrl = URL.createObjectURL(await res.blob());
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
      this.ageS = num(res.headers, "X-C3PO-Age-S");
      this.stale = res.headers.get("X-C3PO-Stale") === "true";
      this.reason = null;
      this.unreachable = false;
    } catch {
      this.reason = "cannot reach the console API";
      this.unreachable = true;
    } finally {
      this.#inFlight = false;
    }
  }

  start(intervalMs = DEFAULT_POLL_MS): void {
    // The costmap publishes at 1 Hz; polling faster only re-fetches the same
    // grid. No-op on the server, where there is no fetch target and no DOM.
    if (typeof window === "undefined" || this.#timer) return;
    void this.#tick();
    this.#timer = setInterval(() => void this.#tick(), intervalMs);
  }

  stop(): void {
    if (this.#timer) clearInterval(this.#timer);
    this.#timer = null;
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
  }
}
