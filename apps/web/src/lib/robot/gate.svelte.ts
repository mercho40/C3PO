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

import { PUBLIC_API_URL } from "$app/env/public";
import { linkFromError, LINK_DETAIL } from "./link-copy";
import { Poller, type PollResult } from "./poller.svelte";

export type GateStatus = {
  enabled: boolean;
  disabled_reason: string | null;
  arm_expires_in_s: number | null;
  cmd_vel_received: number;
  dropped_while_disabled: number;
  clamped: number;
  /** [vx, vy, wz] of the last command forwarded to the robot. */
  last_sent: number[] | null;
  last_sent_age_s: number | null;
  /** Null means no planner is publishing at all. */
  cmd_vel_age_s: number | null;
  clamps?: Record<string, number[]>;
  link?: {
    started?: boolean | null;
    domain_id?: number | null;
    reports_received?: number | null;
  };
};

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
    let res: Response;
    try {
      res = await fetch(`${PUBLIC_API_URL}/telemetry/gate`, {
        credentials: "include",
        signal,
      });
    } catch {
      // Keep the last reading (see `armed`), but say why it is no longer
      // current — a thrown read used to leave `reason` empty.
      this.reason = signal.aborted
        ? "El servidor tardó demasiado en responder."
        : LINK_DETAIL.noApi;
      this.reasonFromBridge = false;
      return "error";
    }
    if (res.status === 401) return "unauthorized";
    if (res.ok) {
      try {
        this.status = (await res.json()) as GateStatus;
      } catch {
        // An aborted or garbled body is no reading — keep the last one
        // flagged as unconfirmed, like any other failed read.
        this.reason = "El servidor devolvió una lectura ilegible.";
        this.reasonFromBridge = false;
        return "error";
      }
      this.reason = null;
      this.reasonFromBridge = false;
      return "ok";
    }
    const body = await res.json().catch(() => null);
    // A 503 is the bridge answering that there is no gate to read — a
    // definitive answer, so the last reading goes. Anything else (bridge
    // unreachable, back down) leaves it as an unconfirmed last reading.
    if (res.status === 503) this.status = null;
    const bridgeError =
      res.status === 503
        ? (body as { error?: string } | null)?.error
        : undefined;
    this.reason =
      bridgeError ??
      (res.status === 503
        ? "El puente no expone el estado de la navegación."
        : LINK_DETAIL[linkFromError(res.status, body)]);
    this.reasonFromBridge = bridgeError !== undefined;
    return "error";
  }
}
