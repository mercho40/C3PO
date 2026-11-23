/**
 * The teleop stream on /vr-control: one WebSocket straight to the bridge.
 *
 * A second, deliberately separate transport: a WebSocket straight to
 * `apps/bridge/src/bridge/teleop/server.py` (port 8767), carrying head
 * yaw + both wrists + finger closure at ~30Hz. See that module and
 * `#lib/teleop/stream.ts` for why this does not go through apps/back.
 *
 * While it is open it becomes the ONLY commander of locomotion: the
 * `walk_velocity` loop (`vr-walk.svelte.ts`) is suspended, and the walk buttons
 * feed the stream's `walk` axis instead. Two independent writers of
 * SetVelocity would silently fight, last-writer-wins, with neither aware of
 * the other. `TeleopHost.onCommanding` / `onReleased` are that handover.
 *
 * Moved out of `vr-control.svelte.ts` intact.
 */

import { PUBLIC_TELEOP_HOST, PUBLIC_TELEOP_PORT } from "$app/env/public";
import { createApi } from "#lib/api.js";
import {
  connectTeleop,
  teleopUrl,
  type TeleopFramePayload,
  type TeleopHandle,
  type TeleopState,
  type TeleopStatus,
} from "#lib/teleop/stream.js";

/** What the link reads from, and tells, the rest of the controller. */
export type TeleopHost = {
  /** Observe-only: the socket must not exist (see `VrControl.viewOnly`). */
  viewOnly: () => boolean;
  /** The page has been torn down. */
  destroyed: () => boolean;
  /** The frame the sender is about to transmit (`VrControl`'s frame builder). */
  getFrame: () => TeleopFramePayload;
  /** The socket opened: the bridge is the commander now. */
  onCommanding: () => void;
  /** The socket stopped commanding (closed, failed or disconnected). */
  onReleased: () => void;
};

export class TeleopLink {
  teleopState = $state<TeleopState>("closed");
  teleopDetail = $state("");
  teleopStatus = $state.raw<TeleopStatus | null>(null);
  armsRequested = $state(false);

  #host: TeleopHost;
  #teleop: TeleopHandle | null = null;
  #connectSeq = 0;

  constructor(host: TeleopHost) {
    this.#host = host;
  }

  get streaming(): boolean {
    return this.teleopState === "open";
  }

  //: The bridge holds the teleop lease while this headset commands motion
  //: (C7) — INCLUDING while stalled. `streaming` goes false there, which used to re-open the door for
  //: this page's own velocity loop: the socket is still OPEN, its 33 Hz sender
  //: is still transmitting, and the moment the bridge starts reading again
  //: both writers are live on SetVelocity. `stalled` is a diagnosis, not a
  //: handover.
  get bridgeHolds(): boolean {
    return this.teleopState === "open" || this.teleopState === "stalled";
  }

  /** PARAR straight down the socket, so it works with apps/back down. */
  stop(): void {
    this.#teleop?.stop();
  }

  /**
   * Teardown. Closing the stream sends one last released frame, so the bridge
   * stops on a frame rather than waiting out its staleness timeout.
   */
  close(): void {
    this.#teleop?.close();
    this.#teleop = null;
  }

  async connect(): Promise<void> {
    // Opening this socket is exactly what makes the headset a commander, and
    // the bridge PULLS from it — so one tap would turn a session somebody
    // entered as passive into one that drives, with nothing visible changing
    // inside the headset. The button is disabled too; this is the guard.
    // `viewOnly` alone, not `&& vrActive`: it is already set while the consent
    // prompt is up, and a socket opened in that window would become a live
    // commander the moment the session ended and `viewOnly` reset.
    if (this.#host.viewOnly()) return;
    this.#teleop?.close();
    this.#teleop = null;
    this.teleopStatus = null;
    this.teleopState = "connecting";
    const attempt = ++this.#connectSeq;
    // The bridge's handshake token. null (no secret configured, or back
    // unreachable) connects without one; the bridge decides.
    let token: string | null = null;
    try {
      const res = await createApi(fetch).teleop.token.get({
        fetch: { signal: AbortSignal.timeout(3000) },
      });
      token = res.data?.token ?? null;
    } catch {
      token = null;
    }
    // A disconnect, a second connect, observe-only or teardown arrived meanwhile.
    if (
      attempt !== this.#connectSeq ||
      this.#host.viewOnly() ||
      this.#host.destroyed()
    )
      return;
    const host = PUBLIC_TELEOP_HOST || location.hostname;
    const port = PUBLIC_TELEOP_PORT || "8767";
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const url = teleopUrl(`${protocol}://${host}:${port}`, token);
    this.#teleop = connectTeleop(url, {
      getFrame: () => this.#host.getFrame(),
      onState: (state, detail) => {
        this.teleopState = state;
        this.teleopDetail = detail ?? "";
        if (state === "open") {
          // The bridge is the commander now; make sure this page is not also
          // mid-flight with a walk_velocity call (`WalkController.yieldToStream`).
          this.#host.onCommanding();
          return;
        }
        this.armsRequested = false;
        if (state === "stalled") {
          // Deliberately NOT starting the local loop here. The socket is still
          // open, so the bridge may start reading again at any moment — and
          // two writers on SetVelocity is a worse failure than one that has
          // stopped. The bridge's own 0.4 s staleness watchdog has already
          // brought the robot to rest, so nothing is running away while the
          // operator decides. The status line says to reconnect.
          return;
        }
        this.#host.onReleased();
      },
      onStatus: (status) => (this.teleopStatus = status),
    });
  }

  disconnect(): void {
    this.#connectSeq++; // cancels a connect still waiting for its token
    this.armsRequested = false;
    this.#teleop?.close();
    this.#teleop = null;
    this.teleopState = "closed";
    // Cleared too: the status line renders on `teleopDetail` being truthy, so
    // a leftover stall or "session already active" message kept showing after
    // a deliberate disconnect, as though it were current.
    this.teleopDetail = "";
    this.teleopStatus = null;
    // `close()` sets its own `closed` flag BEFORE the socket's close event
    // fires, so `onState("closed")` never reaches us — which means the branch
    // that normally restarts the local loop does not run. Without this,
    // pressing "Desconectar" in the headset silently ends head-yaw steering:
    // the stream is gone and the loop that would take over was stopped when
    // the stream opened. It only came back if the operator happened to press
    // a walk button.
    this.#host.onReleased();
  }

  toggleArms(): void {
    this.armsRequested = !this.armsRequested;
  }
}
