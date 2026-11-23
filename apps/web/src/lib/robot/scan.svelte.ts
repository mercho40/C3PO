/**
 * The lidar ring, polled for the headset's radar.
 *
 *   back  GET /telemetry/scan  ->  { v, frame, a0_deg, step_deg, max_cm, r_cm,
 *                                    age_s, stale }
 *
 * Plain JSON, unlike the costmap: the ring is ~120 small integers and nulls,
 * which is smaller than the PNG the costmap needs and needs no placement
 * headers — the payload carries its own frame.
 *
 * WHAT COUNTS AS "NO SCAN" IS THE SAME DELIBERATE DISTINCTION the costmap
 * draws, and it matters more here. 503 is the honest answer whenever the nav
 * container is down or the Mid-360 is unplugged, and it carries a hint. A
 * transport failure is a second state. An arriving-but-stale ring is a third.
 * All three would render as a circle with no dots in it, and a circle with no
 * dots reads as "nothing around you" — which is the one sentence this display
 * must never say by accident.
 *
 * THE LAST GOOD RING IS KEPT ON A 503. Blanking it would flicker the radar
 * empty every time a sample is late, and an operator who is walking cannot
 * tell a flicker from a room that just emptied. `stale` and `ageS` say how old
 * it is; the layer dims it and labels it rather than dropping it.
 */

import { createApi } from "#lib/api.js";
import { httpStatus, unreached } from "#lib/api-client.js";
import { parseRing, type ScanRing } from "#lib/webxr/scan-layer.js";
import { Poller, type PollResult } from "./poller.svelte";

/** 4 Hz at the publisher; polling faster only re-fetches the same ring. */
const DEFAULT_POLL_MS = 250;

/**
 * On the console's one polling policy (`Poller`): never two requests in flight
 * (a slow answer must not stack polls — at 4 Hz that becomes a queue of rings
 * the operator will never see, each one older than the last), a timeout on
 * every request, and a 401 sends the operator to log in rather than reading as
 * "lidar no disponible (401)". Not paused while hidden: it only runs while a
 * headset session is open, and an immersive session is what hides the page.
 */
export class ScanFeed extends Poller {
  ring = $state<ScanRing | null>(null);
  /** Seconds since the robot produced this ring, per the bridge. */
  ageS = $state<number | null>(null);
  /** The bridge's verdict, not a threshold recomputed here. */
  stale = $state(false);
  /** Why there is no ring, in words. Null when one is arriving. */
  reason = $state<string | null>(null);
  /** Distinguishes "no lidar is publishing" from "the fetch failed". */
  unreachable = $state(false);

  /** Between start() and stop(): a request aborted outside it was a stop, not a timeout. */
  #polling = false;

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    let res: Awaited<
      ReturnType<ReturnType<typeof createApi>["telemetry"]["scan"]["get"]>
    >;
    try {
      res = await createApi(fetch).telemetry.scan.get({ fetch: { signal } });
    } catch (err) {
      // Eden throws only for a body it could not parse: a ring that arrived
      // and cannot be drawn (below), unless the session that asked is gone.
      if (!this.#polling) throw err;
      this.reason = "el anillo llegó con un formato que no se puede dibujar";
      this.unreachable = false;
      return "error";
    }
    const { data, error } = res;
    if (unreached(error)) {
      // Stopped mid-request: the session that drew this ring is gone, and
      // `Poller` drops the result. A timeout is "no answer", like a refusal.
      if (!this.#polling) throw error!.value;
      this.reason = "no se puede alcanzar la API de la consola";
      this.unreachable = true;
      return "error";
    }
    if (httpStatus(error) === 401) return "unauthorized";
    if (error?.status === 503) {
      const body = error.value as { hint?: string } | null;
      this.reason = body?.hint ?? "no llega /scan";
      this.unreachable = false;
      return "ok";
    }
    if (error) {
      this.reason = `lidar no disponible (${error.status})`;
      this.unreachable = true;
      return "error";
    }

    // back checked the types (bridge/payloads.ts); parseRing still refuses
    // what is typed right and cannot be drawn (a non-positive max_cm).
    const parsed = parseRing(data);
    if (!parsed) {
      // Kept separate from 503 on purpose: "the ring arrived and I cannot
      // draw it" is a bug in this stack, not a robot that is switched off.
      this.reason = "el anillo llegó con un formato que no se puede dibujar";
      this.unreachable = false;
      return "error";
    }

    this.ring = parsed;
    this.ageS = parsed.age_s ?? null;
    this.stale = parsed.stale === true;
    this.reason = null;
    this.unreachable = false;
    return "ok";
  }

  start(intervalMs = DEFAULT_POLL_MS): void {
    this.#polling = true;
    super.start(intervalMs);
  }

  stop(): void {
    this.#polling = false;
    super.stop();
  }
}
