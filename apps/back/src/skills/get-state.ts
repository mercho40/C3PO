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
    "not a map frame. Battery is real on hardware: it comes from the BMS topic and reports " +
    "state of charge, with a fault raised below 20%. A null battery means no BMS message " +
    "has arrived (always the case in sim) — read it as 'unknown', never as 'fine'. The " +
    "fault list carries locally-derived entries (staleness, low battery), never " +
    "robot-reported faults. A null posture/fsm_id on real means no motion controller is " +
    "loaded — call check_motion_mode before concluding the robot is broken.",
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
