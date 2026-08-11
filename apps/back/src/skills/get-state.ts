import { defineSkill, t } from "./define";

export const getState = defineSkill({
  name: "get_state",
  description:
    "Return the robot's current state: pose (world frame), posture (FSM mode label), " +
    "battery percentage, fault list, and raw telemetry. Fast (~ms); use freely to " +
    "ground reasoning about position and capability before issuing locomotion commands. " +
    "Also reports `env` ('stub' | 'isaac' | 'real') — call this first in a session to " +
    "learn which target you are driving. Pose and posture both work on the real robot " +
    "(pose from vendor odometry, posture from the FSM getter). Pose is odometry, so it " +
    "drifts and its origin is wherever the estimator started — good for relative motion, " +
    "not a map frame. Battery is not wired yet and is always null; the fault list carries " +
    "only locally-derived entries (staleness), never robot-reported faults.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 0.05,
  cancellable: false,
  typicalFailureModes: ["bridge_disconnected", "no_state_received_yet"],
  classification: "introspection",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
