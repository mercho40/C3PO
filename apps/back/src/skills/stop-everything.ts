import { defineSkill, t } from "./define";

export const stopEverything = defineSkill({
  name: "stop_everything",
  description:
    "Halt all motion immediately and cancel any in-flight tasks. Safety-critical and fast " +
    "(<1 s). Cancels every running task in the registry AND sends a zero-velocity burst, " +
    "which now dispatches correctly on both targets — the sim velocity channel on 'isaac', " +
    "SET_VELOCITY(0,0,0) on 'real'. On real it additionally sends a damp: zero velocity " +
    "stops the gait, damp zeroes joint stiffness, and both are wanted. Use this when " +
    "something looks wrong, when humans approach the robot, or to abort a sequence " +
    "cleanly. NOT yet live-tested end to end on hardware — verify it in isolation before " +
    "relying on it operationally, and never treat it as a substitute for the physical " +
    "e-stop, which is always the authoritative stop.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 0.5,
  cancellable: false,
  typicalFailureModes: ["bridge_disconnected"],
  classification: "safety",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
