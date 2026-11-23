/**
 * The Nav2 cmd_vel gate — whether something other than an operator can drive.
 *
 *   back  GET /telemetry/gate  ->  bridge `perception_link` gate status
 *
 * `arm_navigation` (danger "high") opens this gate for up to 120 s so Nav2 can
 * move the robot, and the agent can call it from chat. Nothing in the console
 * used to show that the robot was armed for autonomous driving, or for how
 * long. The topbar shows a chip while it is; /health shows the counters.
 *
 * Read-only by construction, like the bridge route behind it: arming and
 * disarming are skills, never a GET.
 *
 * "Unknown" is a first-class answer. A gate we cannot read is NOT a closed gate.
 */

import type { GateStatus } from "@repo/back";
import { createApi } from "#lib/api.js";
import { httpStatus, unreached } from "#lib/api-client.js";
import { linkFromError, LINK_DETAIL } from "./link-copy";
import { Poller, type PollResult } from "./poller.svelte";

/** The gate's own account (perception_link `status()`), typed by back's schema. */
export type { GateStatus };

export class Gate extends Poller {
  status = $state<GateStatus | null>(null);
  /** Why there is no current gate reading. Null when fine. */
  reason = $state<string | null>(null);
  /**
   * True when `reason` is the bridge's own (English) words from a 503 body;
   * false when it is the console's Spanish. Lets a page tag only bridge text
   * `lang="en"` instead of guessing from the spelling.
   */
  reasonFromBridge = $state(false);

  constructor() {
    super({ timeoutMs: 3000, pauseWhenHidden: true });
  }

  /**
   * True when the bridge's last reading said armed — never inferred.
   *
   * Deliberately NOT cleared by a failed read: an armed gate we can no longer
   * read is not a closed gate. Pair it with `failures > 0` (or `reason`) to say
   * "armed, sin datos" rather than hiding the warning.
   */
  get armed(): boolean {
    return this.status?.enabled === true;
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    let res: Awaited<
      ReturnType<ReturnType<typeof createApi>["telemetry"]["gate"]["get"]>
    >;
    try {
      res = await createApi(fetch).telemetry.gate.get({ fetch: { signal } });
    } catch {
      // An aborted or garbled body is no reading — keep the last one
      // flagged as unconfirmed, like any other failed read.
      this.reason = "El servidor devolvió una lectura ilegible.";
      this.reasonFromBridge = false;
      return "error";
    }
    const { data, error } = res;
    if (unreached(error)) {
      // Keep the last reading (see `armed`), but say why it is no longer
      // current — a thrown read used to leave `reason` empty.
      this.reason = signal.aborted
        ? "El servidor tardó demasiado en responder."
        : LINK_DETAIL.noApi;
      this.reasonFromBridge = false;
      return "error";
    }
    if (httpStatus(error) === 401) return "unauthorized";
    if (!error) {
      this.status = data;
      this.reason = null;
      this.reasonFromBridge = false;
      return "ok";
    }
    // A 503 is the bridge answering that there is no gate to read — a
    // definitive answer, so the last reading goes. Anything else (bridge
    // unreachable, a reading back could not vouch for, back down) leaves it
    // as an unconfirmed last reading.
    const body = error.value as { error?: string } | null;
    if (error.status === 503) this.status = null;
    const bridgeError = error.status === 503 ? body?.error : undefined;
    this.reason =
      bridgeError ??
      (error.status === 503
        ? "El puente no expone el estado de la navegación."
        : LINK_DETAIL[linkFromError(error.status, body)]);
    this.reasonFromBridge = bridgeError !== undefined;
    return "error";
  }
}
