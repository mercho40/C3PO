import { defineSkill, t } from "./define";

export const turn = defineSkill({
  name: "turn",
  description:
    "Rotate the G1 in place by a yaw delta. Positive = counterclockwise (left turn from the " +
    "robot's point of view). Pure yaw control — no linear velocity. Stops when within " +
    "tolerance_degrees of the target. Cancellable.",
  parameters: t.Object({
    delta_yaw_radians: t.Number({
      minimum: -6.2832,
      maximum: 6.2832,
      description:
        "How far to rotate, in radians. Positive = left/CCW. 90° left ≈ 1.5708; " +
        "180° ≈ 3.1416 (or -3.1416).",
    }),
    timeout_s: t.Number({
      minimum: 5.0,
      maximum: 120.0,
      default: 30.0,
      description:
        "Maximum seconds to spend rotating before giving up. Walk policy yaw is slow " +
        "under small errors — allow 30s+ per 90° if accuracy matters.",
    }),
    tolerance_degrees: t.Number({
      minimum: 0.5,
      maximum: 20.0,
      default: 3.0,
      description: "Stop when within this many degrees of the target yaw.",
    }),
  }),
  preconditions: ["robot_upright", "no_active_turn_task"],
  expectedDurationSeconds: 12,
  cancellable: true,
  cancellationEffect:
    "Robot ramps yaw rate to zero in ~0.4 s and holds current heading.",
  typicalFailureModes: ["timeout", "no_pose"],
  classification: "locomotion",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
