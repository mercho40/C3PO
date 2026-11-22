// GENERATED from contracts/bridge-payloads.schema.json. Do not edit: cd apps/bridge && uv run python scripts/export_contracts.py
//
// The bridge's HTTP payloads as TypeScript, so the console's hand-written
// types can be compared with them at compile time (apps/back and apps/web
// contracts.test.ts). Types only: nothing imports a value from here.

export type Geofence = {
  radius_m: number | null;
  anchor: number[] | null;
  distance_m: number | null;
  inside: boolean | null;
};

export type RobotPose = {
  x_meters_world: number;
  y_meters_world: number;
  z_meters_world?: number;
  yaw_radians_world: number;
};

export type RobotState = {
  pose: RobotPose | null;
  battery_pct: number | null;
  posture: string;
  faults: string[];
  env: string;
  stub: boolean;
  raw?: RobotStateRaw;
};

export type RobotStateRaw = {
  tick: number;
  mode_machine: number;
  motor_count: number;
  lowstate_messages_received: number;
  lowstate_age_s?: number;
  pose_source: string;
  pose_messages_received: number;
  pose_age_s?: number | null;
  fsm_id?: number | null;
  fsm_mode?: number | null;
  fsm_age_s?: number | null;
  battery_soh_pct?: number | null;
  battery_current_ma?: number | null;
  battery_messages_received?: number;
  battery_age_s?: number | null;
};

export type SafetyCaps = {
  linear_mps: number;
  yaw_rps: number;
  accel_mps2: number;
  yaw_accel_rps2: number;
};

export type SafetyRouteError = "forbidden" | "confirmation_not_configured" | "not_confirmable" | "invalid_arguments" | "newer_stop" | "ack_not_recorded";

export type SafetyStatusBody = {
  stop_standing: boolean;
  stopped_at: number | null;
  acknowledged_at: number | null;
  caps: SafetyCaps;
  person_rule: boolean;
  stale_policy: string;
  t2_confirm: boolean;
  confirm_configured: boolean;
  pending_confirmations: string[];
  perception_fresh: boolean;
  obstacles_fresh: boolean;
  geofence: Geofence;
  nearest_person_m: number | null;
  last_command: number[];
  last_command_age_s: number | null;
  last_reason: string | null;
  face_greet: Record<string, unknown>;
};

export type StopAckRefused = {
  stop_standing: boolean;
  stopped_at: number | null;
  acknowledged_at: number | null;
  error: "forbidden" | "confirmation_not_configured" | "not_confirmable" | "invalid_arguments" | "newer_stop" | "ack_not_recorded";
};

export type StopAcknowledged = {
  stop_standing: boolean;
  stopped_at: number | null;
  acknowledged_at: number | null;
  acknowledged: boolean;
  was_standing: boolean;
};
