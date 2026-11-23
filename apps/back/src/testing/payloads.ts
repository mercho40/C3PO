/**
 * Bridge answers that pass their schemas (bridge/payloads.ts), for tests.
 *
 * Each is the smallest body its bridge route really sends, typed against the
 * schema's static type so a schema change shows up here at compile time.
 * Tests only.
 */
import type {
  FacesView,
  GateStatus,
  ScanRingBody,
  Surroundings,
  VoiceTelemetry,
} from "../bridge/payloads";
import type {
  RobotState,
  SafetyStatusBody,
} from "../../../../contracts/bridge-payloads";

export const STATE: RobotState = {
  pose: { x_meters_world: 1, y_meters_world: 2, yaw_radians_world: 0 },
  battery_pct: 80,
  posture: "stand",
  faults: [],
  env: "stub",
  stub: true,
};

export const SAFETY_STATUS: SafetyStatusBody = {
  stop_standing: false,
  stopped_at: null,
  acknowledged_at: null,
  caps: { linear_mps: 0.3, yaw_rps: 0.5, accel_mps2: 0.5, yaw_accel_rps2: 1 },
  person_rule: true,
  stale_policy: "stop",
  t2_confirm: true,
  confirm_configured: true,
  pending_confirmations: [],
  perception_fresh: true,
  obstacles_fresh: true,
  geofence: { radius_m: null, anchor: null, distance_m: null, inside: null },
  nearest_person_m: null,
  last_command: [0, 0, 0],
  last_command_age_s: null,
  last_reason: null,
  face_greet: {},
};

export const SURROUNDINGS: Surroundings = {
  version: 1,
  sources: { detector: "ok", lidar: "stale" },
  objects: [{ label: "person", range_m: 1.2, bearing_deg: 10 }],
};

export const SCAN: ScanRingBody = {
  v: 1,
  frame: "base",
  a0_deg: -180,
  step_deg: 3,
  max_cm: 800,
  r_cm: [120, null, 340],
  age_s: 0.2,
  stale: false,
};

export const VOICE_TELEMETRY: VoiceTelemetry = {
  running: true,
  error: null,
  always_listening: false,
  audio_source: "headset",
  mic_ever_open: true,
  seconds_since_audio: 3.5,
  utterances: 1,
  recent: [{ text: "hola", kind: "speech", age_s: 3.5 }],
};

export const GATE: GateStatus = {
  enabled: false,
  disabled_reason: "disabled",
  arm_expires_in_s: null,
  cmd_vel_received: 0,
  dropped_while_disabled: 0,
  clamped: 0,
  last_sent: null,
  last_sent_age_s: null,
  cmd_vel_age_s: null,
  clamps: { vx: [-0.3, 0.3] },
  link: { started: true, domain_id: 0, reports_received: 4 },
};

export const FACES: FacesView = {
  settings: {
    greet: true,
    greet_unknown: false,
    gate: false,
    listen_after_greet: false,
  },
  people: [
    {
      id: "p1",
      name: "Ana",
      role: "operator",
      enrolled_at: "2026-11-01T10:00:00Z",
      has_photo: true,
    },
  ],
  unknown_seen: { count: 0, last_age_s: null },
  models: true,
};
