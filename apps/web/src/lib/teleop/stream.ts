/**
 * Client for the bridge's teleop stream (`bridge/teleop/server.py`, port 8767).
 *
 * Why this bypasses `apps/back` entirely, unlike every other control on the
 * page: teleop is a 30 Hz stream of expiring setpoints, not a sequence of
 * tasks. Routing it through Eden/Elysia and MCP would put a JSON-RPC
 * round-trip, a task-registry entry and a progress notification in the path of
 * every frame. Same reasoning, and the same "reach it over an SSH tunnel"
 * deployment posture, as the camera relay on 8766.
 *
 * The consequence to be honest about: this socket has no authentication of its
 * own, and neither does the relay. Both are loopback-bound on the Jetson and
 * expected to be tunnelled. Do not expose either to a network you do not own.
 *
 * Two policies live here rather than in the WebXR module or in the page:
 *
 * 1. **Decimation.** WebXR fires at 72-120 Hz. The bridge dispatches at 20 Hz
 *    and the arm loop runs at 50. Sending every XR frame would be 3-6x the
 *    traffic for setpoints that are discarded on arrival, so frames are sent
 *    on a fixed interval with the most recent sample.
 * 2. **Send even when idle.** A frame still goes out while the operator holds
 *    nothing, carrying `enabled: false`. That is what makes the bridge's
 *    staleness dead-man mean something: silence has to be reserved for "the
 *    client is gone", so it cannot also mean "the client is fine and idle".
 */

export type TeleopState = "connecting" | "open" | "closed" | "error";

export type TeleopStatus = {
  frames_received: number;
  frames_rejected: number;
  calibrated: boolean;
  arm_length_m: number;
  deadman_tripped: boolean;
  /** Latched by `stop_everything`. Clears only when the operator lets go. */
  stopped_by_estop: boolean;
  task_id: string;
  moving: boolean;
  hands: string;
  arm: {
    engaged: boolean;
    weight: number;
    enabled_by_env: boolean;
    sim_mode: string;
  };
  arm_error: string | null;
};

export type TeleopFramePayload = {
  enabled: boolean;
  walk: number;
  arms: boolean;
  head: { yaw: number; pos: [number, number, number] };
  hands: {
    left?: { tracked: boolean; pos?: number[]; quat?: number[]; grip?: number };
    right?: {
      tracked: boolean;
      pos?: number[];
      quat?: number[];
      grip?: number;
    };
  };
};

export type TeleopHandle = {
  /** Replace the payload sent on the next tick. Cheap; call it per XR frame. */
  update: (payload: TeleopFramePayload) => void;
  close: () => void;
};

export type TeleopCallbacks = {
  onState: (state: TeleopState, detail?: string) => void;
  onStatus?: (status: TeleopStatus) => void;
};

export const PROTOCOL_VERSION = 1;
const SEND_INTERVAL_MS = 33; // ~30 Hz

/** The payload for "operator is holding nothing", used before the first update. */
export function idlePayload(): TeleopFramePayload {
  return {
    enabled: false,
    walk: 0,
    arms: false,
    head: { yaw: 0, pos: [0, 1.6, 0] },
    hands: {},
  };
}

export function connectTeleop(
  url: string,
  callbacks: TeleopCallbacks,
): TeleopHandle {
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  let seq = 0;
  let payload = idlePayload();
  let closed = false;

  callbacks.onState("connecting");
  try {
    socket = new WebSocket(url);
  } catch (err) {
    callbacks.onState(
      "error",
      err instanceof Error ? err.message : String(err),
    );
    return { update: () => {}, close: () => {} };
  }

  socket.addEventListener("open", () => {
    callbacks.onState("open");
    timer = setInterval(() => {
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      // `bufferedAmount` guards the one failure mode a fixed-rate sender has:
      // if the link stalls, queued frames pile up and every one of them is
      // already stale by the time it arrives. Skipping a tick lets the socket
      // drain and keeps what does arrive current.
      if (socket.bufferedAmount > 4096) return;
      socket.send(
        JSON.stringify({
          v: PROTOCOL_VERSION,
          seq: seq++,
          t: Math.round(performance.now()),
          ...payload,
        }),
      );
    }, SEND_INTERVAL_MS);
  });

  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") return;
    try {
      const parsed = JSON.parse(event.data);
      if (parsed?.type === "status")
        callbacks.onStatus?.(parsed as TeleopStatus);
    } catch {
      // Status is decorative. A malformed one must not disturb the sender.
    }
  });

  socket.addEventListener("error", () => {
    if (!closed)
      callbacks.onState(
        "error",
        "No se pudo conectar con el puente de teleoperación.",
      );
  });

  socket.addEventListener("close", (event) => {
    if (timer) clearInterval(timer);
    timer = null;
    if (closed) return;
    // 1013 "try again later" is the bridge refusing a second operator. Worth
    // saying plainly: silently retrying would fight the session that has it.
    callbacks.onState(
      "closed",
      event.code === 1013
        ? "Ya hay una sesión de teleoperación activa."
        : event.reason || "",
    );
  });

  return {
    update: (next) => {
      payload = next;
    },
    close: () => {
      closed = true;
      if (timer) clearInterval(timer);
      timer = null;
      // Best effort: tell the bridge we are letting go before the socket
      // drops, so it stops on a frame rather than on a staleness timeout.
      if (socket?.readyState === WebSocket.OPEN) {
        try {
          socket.send(
            JSON.stringify({
              v: PROTOCOL_VERSION,
              seq: seq++,
              t: 0,
              ...idlePayload(),
            }),
          );
        } catch {
          // The close below is what actually matters.
        }
      }
      socket?.close();
      socket = null;
    },
  };
}
