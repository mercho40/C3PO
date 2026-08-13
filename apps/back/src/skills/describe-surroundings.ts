import { defineSkill, t } from "./define";

export const describeSurroundings = defineSkill({
  name: "describe_surroundings",
  description:
    "Return a compact egocentric snapshot of what the robot can perceive: nearby objects " +
    "with range and bearing, free space per sector, remembered landmarks in view, and a " +
    "per-source health report. Bearings are degrees, 0 straight ahead, positive to the LEFT " +
    "(counter-clockwise) — the same sign convention as turn's delta_yaw_radians. " +
    "Read `sources` before acting on the content: an empty object list with the detector " +
    "OFFLINE means nothing was looked at, NOT that the path is clear. Anything degraded is " +
    "also stated in plain language in `notes`, which is the field most worth reading. " +
    "Object lists are capped, and whatever is dropped is counted in objects_omitted rather " +
    "than silently lost.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 0.1,
  cancellable: false,
  typicalFailureModes: ["bridge_disconnected", "no_pose"],
  classification: "perception",
  dangerLevel: "low",
  status: "real",
  // The contract is real and testable, but every perception source behind it
  // is still offline — there is no detector and no LiDAR consumer yet, so it
  // currently reports "offline" for everything rather than describing a scene.
  works: { sim: true, real: true },
});
