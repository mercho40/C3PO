/**
 * Is the robot actually moving? Measured from the pose, not read off the FSM.
 *
 * The FSM token says which CONTROLLER is loaded, and a gait controller stands
 * perfectly still at zero stick: the bridge reads FSM 802 ("run") on a robot
 * that is not moving at all (g1_protocol.py MODE_LABEL notes), and 500/501
 * (walk) are the same. Painting those as "moving" — cyan, glowing, striding —
 * spent the console's one reserved signal on a robot standing in place. Motion
 * is only claimed when two consecutive pose readings show it.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

export type Motion = "moving" | "still" | "unknown";

export type PoseSample = {
  x: number;
  y: number;
  /** Radians, REP-103. */
  yaw: number;
  /** Wall-clock ms the reading was received. */
  t: number;
};

/**
 * Translation that counts as motion between two readings. Matches the trail's
 * jitter gate (live-state.svelte.ts) so "moved" means the same thing on the
 * map and in the headline.
 */
export const MOVE_EPS_M = 0.05;
/** Rotation that counts as motion — a turn in place moves no metres. ~3°. */
export const TURN_EPS_RAD = 0.05;
/**
 * Readings further apart than this say nothing about NOW: a pose that
 * changed over the last 20 s may have stopped 18 s ago.
 */
export const MAX_GAP_MS = 6000;

/** Shortest signed angle from `a` to `b`, in (-π, π]. */
export function angleDelta(a: number, b: number): number {
  let d = (b - a) % (2 * Math.PI);
  if (d <= -Math.PI) d += 2 * Math.PI;
  if (d > Math.PI) d -= 2 * Math.PI;
  return d;
}

/** Motion between two consecutive readings. */
export function classifyMotion(
  prev: PoseSample | null,
  next: PoseSample | null,
): Motion {
  if (!prev || !next) return "unknown";
  const dt = next.t - prev.t;
  if (dt <= 0 || dt > MAX_GAP_MS) return "unknown";
  const moved = Math.hypot(next.x - prev.x, next.y - prev.y) > MOVE_EPS_M;
  const turned = Math.abs(angleDelta(prev.yaw, next.yaw)) > TURN_EPS_RAD;
  return moved || turned ? "moving" : "still";
}

/**
 * Feeds `classifyMotion` from the live poll.
 *
 * Call `observe(pose, lastOkAt)` from a `$derived`: it is idempotent for a
 * timestamp it has already seen, so re-evaluation never fabricates a second
 * sample, and the classification only advances when the poller does.
 */
export class MotionTracker {
  #prev: PoseSample | null = null;
  #last: PoseSample | null = null;
  #result: Motion = "unknown";

  observe(
    pose:
      | {
          x_meters_world: number;
          y_meters_world: number;
          yaw_radians_world: number;
        }
      | null
      | undefined,
    at: number | null | undefined,
    /** The pose is known to be frozen (a `stale_pose_*` fault). */
    poseStale = false,
  ): Motion {
    // A frozen pose reads as perfectly still — the most convincing lie
    // available. Without fresh odometry nothing can be said about motion.
    if (!pose || at == null || poseStale) {
      this.#prev = null;
      this.#last = null;
      this.#result = "unknown";
      return this.#result;
    }
    if (this.#last && this.#last.t === at) return this.#result;
    const sample: PoseSample = {
      x: pose.x_meters_world,
      y: pose.y_meters_world,
      yaw: pose.yaw_radians_world,
      t: at,
    };
    this.#prev = this.#last;
    this.#last = sample;
    this.#result = classifyMotion(this.#prev, this.#last);
    return this.#result;
  }
}
