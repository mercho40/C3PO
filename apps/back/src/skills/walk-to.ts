import { defineSkill, t } from "./define";

export const walkTo = defineSkill({
  name: "walk_to",
  description:
    "Drive the G1 toward a world-frame XY position and stop within stop_distance_m. " +
    "Body-frame velocity loop with yaw correction + yaw-gating (turn first, walk second). " +
    "Cancellable mid-flight via cancel_task or stop_everything. Returns a task_id and " +
    "streams progress; the final result includes arrival status, displacement, and final pose.",
  parameters: t.Object({
    target_x_meters_world_frame: t.Number({
      description: "Target X coordinate in meters, world frame.",
    }),
    target_y_meters_world_frame: t.Number({
      description: "Target Y coordinate in meters, world frame.",
    }),
    stop_distance_m: t.Optional(
      t.Number({
      minimum: 0.3,
      maximum: 5.0,
      default: 1.0,
      description:
        "How close to the target the robot should stop. Smaller values mean closer; " +
        "minimum 0.3 m to avoid collisions.",
    }),
    ),
    timeout_s: t.Optional(
      t.Number({
      minimum: 5.0,
      maximum: 300.0,
      default: 60.0,
      description:
        "Maximum seconds to spend walking before giving up. The current Isaac Sim " +
        "policy walks at ~10-15% of commanded velocity, so allow generous time per metre.",
    }),
    ),
  }),
  preconditions: ["robot_upright", "battery_pct_gt_15", "no_active_walk_task"],
  expectedDurationSeconds: 20,
  cancellable: true,
  cancellationEffect:
    "Robot ramps velocity to zero in ~0.4 s and stops; final pose returned in result.",
  typicalFailureModes: ["path_blocked", "timeout", "no_pose"],
  classification: "locomotion",
  dangerLevel: "medium",
  status: "real",
  // The original blocker (no pose on real) is fixed — pose now comes from
  // rt/odommodestate, and SET_VELOCITY (api_id 7105) is wired and confirmed
  // accepted by firmware. So this should work on real now.
  //
  // It stays `real: false` because "should work" is not "does work": non-zero
  // velocity has never been executed on this robot, the axis sign conventions
  // are unverified, and the control gains are fitted to a sim policy that runs
  // at ~10-15% of commanded velocity, so distances will be wrong on hardware.
  // Flipping this true would let the agent command untested motion on a machine
  // with people near it. Flip it after a supervised motion window measures the
  // signs and scaling — not before.
  works: { sim: true, real: false },
});
