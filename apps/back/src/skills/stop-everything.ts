import { defineSkill, t } from "./define";

export const stopEverything = defineSkill({
  name: "stop_everything",
  description:
    "Halt all motion immediately and cancel any in-flight tasks. Safety-critical and fast " +
    "(<1 s). Cancels every running task in the registry AND sends a zero-velocity burst to " +
    "the locomotion channel (sim-only — no-op on real G1, which doesn't subscribe to that " +
    "channel) plus, on real G1, a direct damp dispatch via g1_rpc. Use this when something " +
    "looks wrong, when humans approach the robot, or when you want to abort a sequence " +
    "cleanly. NOTE (2026-08-07): the real-G1 damp fallback is new and not yet live-tested " +
    "on hardware — smoke-test it in isolation before relying on it operationally.",
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
