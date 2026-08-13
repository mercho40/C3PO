import { defineSkill, t } from "./define";

export const walkVelocity = defineSkill({
  name: "walk_velocity",
  description:
    "Command a raw body-frame velocity — real G1 hardware only, no pose needed. Unlike " +
    "walk_to/turn (blocked on real hardware, no world-frame pose source wired), this is " +
    "open-loop: the same fire-and-forget pattern xr_teleoperate's own controller-button " +
    "locomotion uses. The firmware sustains the velocity for duration_s and stops on its " +
    "own. Not applicable in Isaac Sim — walk_to/turn give closed-loop pose control there, " +
    "strictly better than this fallback. Velocity and duration are hard-capped server-side " +
    "regardless of what's requested (0.3 m/s, 0.3 rad/s, 3s max per call) — no pose feedback " +
    "means no way to detect early if something's wrong, so keep each blind command small; " +
    "call repeatedly for sustained motion.",
  parameters: t.Object({
    vx: t.Number({
      minimum: -0.3,
      maximum: 0.3,
      default: 0,
      description: "Forward/backward body-frame velocity, m/s. Positive = forward.",
    }),
    vy: t.Number({
      minimum: -0.3,
      maximum: 0.3,
      default: 0,
      description: "Lateral (strafe) body-frame velocity, m/s. Positive = left.",
    }),
    vyaw: t.Number({
      minimum: -0.3,
      maximum: 0.3,
      default: 0,
      description: "Yaw rate, rad/s. Positive = counterclockwise.",
    }),
    duration_s: t.Number({
      minimum: 0.1,
      maximum: 3.0,
      default: 1.0,
      description: "How long to sustain this velocity before automatically stopping.",
    }),
  }),
  preconditions: ["fsm_state_walk_or_similar"],
  expectedDurationSeconds: 1.5,
  cancellable: true,
  cancellationEffect: "Sends an immediate zero-velocity command rather than waiting out duration_s.",
  typicalFailureModes: ["rpc_error", "not_applicable_in_sim"],
  classification: "locomotion",
  dangerLevel: "medium",
  status: "real",
  // NOT YET LIVE-TESTED (2026-08-13) — api_id 7105/SetVelocity is confirmed to
  // exist in the official unitree_sdk2py SDK and shares the same DDS RPC
  // pattern as the verified-live posture/gesture calls, but hasn't itself
  // been dispatched against real hardware yet. First live call should be a
  // short, small vx before trusting this further.
  works: { sim: false, real: true },
});
