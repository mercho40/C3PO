/**
 * How the console prints robot numbers — in one place.
 *
 * Battery tone, position and distance used to be formatted separately in the
 * topbar, the dashboard and the live map, and they had already drifted: the
 * topbar's battery glyph switched at 60 % while its colour switched at 40 %,
 * position was written four different ways, and the map coloured nothing at all.
 * A reading that looks different on two panels of the same screen reads as two
 * different readings.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

/** The robot's name as printed on the console. One robot, one name. */
export const UNIT_ID = "BIPED-01";

export type BatteryLevel = "unknown" | "critical" | "low" | "ok" | "full";

/**
 * Battery bands. 20 % is the vendor's own low-battery threshold (the bridge
 * raises `low_battery_*` below it); 40 % is where a session should be planned
 * around a dock; above 60 % the glyph reads as full.
 */
export function batteryLevel(pct: number | null | undefined): BatteryLevel {
  if (pct == null || Number.isNaN(pct)) return "unknown";
  if (pct <= 20) return "critical";
  if (pct <= 40) return "low";
  if (pct <= 60) return "ok";
  return "full";
}

/** Text colour per battery band. "ok" and "full" are both plain ink. */
export const BATTERY_TONE: Record<BatteryLevel, string> = {
  unknown: "text-ink-mute",
  critical: "text-danger-soft",
  low: "text-warn",
  ok: "text-ink",
  full: "text-ink",
};

export function batteryTone(pct: number | null | undefined): string {
  return BATTERY_TONE[batteryLevel(pct)];
}

/** Whole-percent battery, or null when there is no reading. */
export function batteryPct(pct: number | null | undefined): number | null {
  return pct == null || Number.isNaN(pct) ? null : Math.round(pct);
}

/** Metres with two decimals and a comma-free minus — `x 1.24 · y -0.53 m`. */
export function formatPose(
  pose: { x_meters_world: number; y_meters_world: number } | null | undefined,
): string {
  if (!pose) return "—";
  return `x ${pose.x_meters_world.toFixed(2)} · y ${pose.y_meters_world.toFixed(2)} m`;
}

/** Heading in whole degrees, REP-103 (0° = +x, positive counter-clockwise). */
export function yawDegrees(yawRad: number | null | undefined): number | null {
  return yawRad == null ? null : Math.round((yawRad * 180) / Math.PI);
}

/**
 * Distance travelled. Metres below 1 km, because a lab session is tens of
 * metres and "0.00 km" reads as a robot that never moved.
 */
export function formatDistance(meters: number): string {
  if (meters < 1000) {
    return `${meters < 10 ? meters.toFixed(1) : Math.round(meters)} m`;
  }
  return `${(meters / 1000).toFixed(2)} km`;
}

/**
 * An age the robot reported, in seconds, as Spanish operator copy:
 * "hace 3 s", "hace 4 min", "hace 2 h". Null renders as "—" — never as "now".
 */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  if (seconds < 1) return "ahora";
  if (seconds < 60) return `hace ${Math.round(seconds)} s`;
  if (seconds < 3600) return `hace ${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `hace ${Math.round(seconds / 3600)} h`;
  return `hace ${Math.round(seconds / 86400)} d`;
}

/** Same, from a wall-clock timestamp in ms. */
export function formatSince(
  timestampMs: number | null | undefined,
  nowMs: number,
): string {
  if (timestampMs == null) return "—";
  return formatAge(Math.max(0, (nowMs - timestampMs) / 1000));
}

/** Latency in ms, or "—". */
export function formatLatency(ms: number | null | undefined): string {
  return ms == null ? "—" : `${Math.round(ms)} ms`;
}
