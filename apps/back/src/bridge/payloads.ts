/**
 * The bridge's JSON payloads as TypeBox, for the routes that relay them.
 *
 * WHY. back's proxy routes returned `unknown` (or a raw `Response`), so Eden
 * typed nothing past them and the console fetched them by hand, restating
 * each shape. A route that declares `response:` with these is typed end to end
 * in apps/web, and a bridge that answers an unexpected shape is caught HERE,
 * as a 502 `bridge_bad_payload` naming the field, instead of surfacing as a
 * dashboard reading `undefined`.
 *
 * TWO SOURCES, ONE RULE.
 *
 *   - Payloads `contracts/bridge-payloads.schema.json` describes (the robot
 *     state, the safety status, the /safety/ack bodies) are DERIVED from it by
 *     `fromContract`: the bridge regenerates that file from its own TypedDicts
 *     (apps/bridge/scripts/export_contracts.py), so a change there reaches back
 *     without anyone restating it. The static type comes from the generated
 *     `bridge-payloads.ts`, made from the same JSON, so the two cannot disagree.
 *     A small converter rather than `t.Unsafe(json)`: Elysia validates and
 *     normalises with TypeBox's compiler, which needs real TypeBox kinds and
 *     no `$ref`s; the converter inlines the refs and throws on any keyword it
 *     does not understand, so a contract that grows one fails loudly here.
 *   - The telemetry and «Caras» payloads are not in contracts/ yet, so they are
 *     hand-written below, from the bridge routes they mirror (named on each).
 *     They used to be restated in apps/web; this is now the one copy.
 *
 * EXTRA FIELDS PASS, everywhere. JSON Schema allows properties it does not
 * name, and so does every object here (`additionalProperties: true`): a field
 * the bridge adds must neither fail a route nor be silently stripped by
 * Elysia's response normalisation. What fails is a field the console reads
 * that is missing or of the wrong type.
 *
 * NOTHING ON THE STOP PATH IS CHECKED. PARAR, `soft_stop`, the /tasks estop
 * and «Reanudar» (/safety/ack) declare no response schema: a stop must never
 * be answered 502 because the bridge described its success in a new way.
 * `StopAckBody` below is a type only.
 */
import {
  Type,
  type Static,
  type TSchema,
  type TUnsafe,
} from "@sinclair/typebox";
import { TypeCompiler } from "@sinclair/typebox/compiler";
import type { ValueError } from "@sinclair/typebox/errors";

import contract from "../../../../contracts/bridge-payloads.schema.json";
import type {
  RobotState,
  SafetyStatusBody,
  StopAckRefused,
  StopAcknowledged,
} from "../../../../contracts/bridge-payloads";

// ---------------------------------------------------------------------------
// contracts/ → TypeBox

/** The JSON Schema subset the bridge's exporter writes. */
type JsonSchema = {
  type?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean;
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  enum?: (string | number | boolean)[];
  $ref?: string;
};

const DEFS = (contract as unknown as { $defs: Record<string, JsonSchema> })
  .$defs;
/** Keywords that describe and never constrain. */
const ANNOTATIONS = new Set(["$comment", "description", "title"]);
const UNDERSTOOD = new Set([
  "type",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "anyOf",
  "enum",
  "$ref",
]);

/** One JSON Schema node as TypeBox. Throws on what it does not understand. */
export function toTypeBox(node: JsonSchema, at = "#"): TSchema {
  for (const key of Object.keys(node))
    if (!UNDERSTOOD.has(key) && !ANNOTATIONS.has(key))
      throw new Error(`contracts: unsupported keyword "${key}" at ${at}`);
  if (node.$ref) {
    const name = node.$ref.replace(/^#\/\$defs\//, "");
    const def = DEFS[name];
    if (!def) throw new Error(`contracts: unresolved ${node.$ref} at ${at}`);
    return toTypeBox(def, node.$ref);
  }
  if (node.anyOf)
    return Type.Union(
      node.anyOf.map((n, i) => toTypeBox(n, `${at}/anyOf/${i}`)),
    );
  if (node.enum) return Type.Union(node.enum.map((v) => Type.Literal(v)));
  switch (node.type) {
    case "object": {
      const required = new Set(node.required ?? []);
      const props: Record<string, TSchema> = {};
      for (const [k, v] of Object.entries(node.properties ?? {})) {
        const s = toTypeBox(v, `${at}/properties/${k}`);
        props[k] = required.has(k) ? s : Type.Optional(s);
      }
      // JSON Schema's default: properties it does not name are allowed.
      return Type.Object(props, {
        additionalProperties: node.additionalProperties ?? true,
      });
    }
    case "array":
      return Type.Array(
        node.items ? toTypeBox(node.items, `${at}/items`) : Type.Unknown(),
      );
    case "string":
      return Type.String();
    case "number":
      return Type.Number();
    case "integer":
      return Type.Integer();
    case "boolean":
      return Type.Boolean();
    case "null":
      return Type.Null();
    default:
      throw new Error(`contracts: unsupported type "${node.type}" at ${at}`);
  }
}

/** The contract's `$defs[name]`, as a schema whose static type is `T`. */
export function fromContract<T>(
  name: keyof (typeof contract)["$defs"],
): TUnsafe<T> {
  // `Unsafe` keeps the converted schema's own kind (Object), so Elysia's
  // compiler validates it like any `t.Object`; it only overrides the type.
  return Type.Unsafe<T>(toTypeBox(DEFS[name]!, `#/$defs/${name}`));
}

// ---------------------------------------------------------------------------
// Checking a payload

export type PayloadFailure = { path: string; message: string };

/**
 * The deepest error, unions included. A union reports "Expected union value"
 * at its own path; `pose: {x_meters_world: "1"}` is more use to whoever reads
 * the log as `/pose/x_meters_world: Expected number`, the member that nearly
 * matched.
 */
function deepest(errors: Iterable<ValueError>): ValueError | undefined {
  let best: ValueError | undefined;
  for (const e of errors) {
    const inner = e.errors.length
      ? deepest(e.errors.flatMap((it) => [...it]))
      : undefined;
    for (const c of [e, inner])
      if (c && (!best || c.path.length > best.path.length)) best = c;
  }
  return best;
}

/** A compiled check: the value, typed, or where it went wrong. */
export function payloadCheck<S extends TSchema>(schema: S) {
  const compiled = TypeCompiler.Compile(schema);
  return (
    value: unknown,
  ): { ok: true; value: Static<S> } | ({ ok: false } & PayloadFailure) => {
    if (compiled.Check(value)) return { ok: true, value };
    const worst = deepest(compiled.Errors(value));
    return {
      ok: false,
      path: worst?.path || "/",
      message: worst?.message ?? "does not match the schema",
    };
  };
}

/** What a proxy route answers instead of a body it could not vouch for. */
export const BridgeFailureBody = Type.Object(
  {
    error: Type.String(),
    /** `bridge_bad_payload` only: the first field that did not match. */
    path: Type.Optional(Type.String()),
    message: Type.Optional(Type.String()),
  },
  { additionalProperties: true },
);

/**
 * The bridge's own 503: up, with nothing to report, and (usually) why. Passed
 * through so the console can say WHY there is nothing.
 */
export const BridgeUnavailableBody = Type.Object(
  { error: Type.Optional(Type.String()), hint: Type.Optional(Type.String()) },
  { additionalProperties: true },
);
export type BridgeUnavailableBody = Static<typeof BridgeUnavailableBody>;
export const checkUnavailable = payloadCheck(BridgeUnavailableBody);

const lastLogged = new Map<string, string>();

/**
 * The 502 for a payload that failed its schema. Logged once per route until
 * the failure changes: these routes are polled at up to 4 Hz.
 */
export function badPayload(route: string, failure: PayloadFailure) {
  const line = `${failure.path}: ${failure.message}`;
  if (lastLogged.get(route) !== line) {
    lastLogged.set(route, line);
    console.warn(`[back] bridge_bad_payload on ${route} at ${line}`);
  }
  return {
    error: "bridge_bad_payload" as const,
    path: failure.path,
    message: failure.message,
  };
}

// ---------------------------------------------------------------------------
// From contracts/

export const RobotStateSchema = fromContract<RobotState>("RobotState");
export const SafetyStatusSchema =
  fromContract<SafetyStatusBody>("SafetyStatusBody");

/** /safety/ack's answer, either way. A type only: see the header. */
export type StopAckBody = StopAcknowledged | StopAckRefused;

// ---------------------------------------------------------------------------
// Hand-written (not in contracts/ yet)

const open = { additionalProperties: true } as const;
const Nullable = <S extends TSchema>(s: S) => Type.Union([s, Type.Null()]);

/** One thing perception reports, egocentric: 0° ahead, POSITIVE LEFT. */
export const SeenObject = Type.Object(
  {
    label: Type.String(),
    range_m: Type.Number(),
    bearing_deg: Type.Number(),
    confidence: Type.Optional(Type.Number()),
    age_s: Type.Optional(Type.Number()),
  },
  open,
);
export type SeenObject = Static<typeof SeenObject>;

/** Per-source health. "offline" is a first-class answer, never absence. */
export const SourceStatus = Type.Union([
  Type.Literal("ok"),
  Type.Literal("stale"),
  Type.Literal("offline"),
]);
export type SourceStatus = Static<typeof SourceStatus>;

/** GET /telemetry/surroundings: the D7 snapshot (`surroundings_snapshot()`). */
export const SurroundingsSchema = Type.Object(
  {
    version: Type.Number(),
    sources: Type.Record(Type.String(), SourceStatus),
    objects: Type.Optional(Type.Array(SeenObject)),
    objects_omitted: Type.Optional(Type.Number()),
    free_space: Type.Optional(Type.Record(Type.String(), Type.Number())),
    landmarks: Type.Optional(Type.Array(SeenObject)),
    notes: Type.Optional(Type.Array(Type.String())),
    pose: Type.Optional(Type.Record(Type.String(), Type.Number())),
    report_age_s: Type.Optional(Type.Number()),
  },
  open,
);
export type Surroundings = Static<typeof SurroundingsSchema>;

/** GET /telemetry/scan: the lidar ring (mcp_server.py `scan_json`). */
export const ScanRingSchema = Type.Object(
  {
    v: Type.Optional(Type.Number()),
    frame: Type.Optional(Type.String()),
    a0_deg: Type.Number(),
    step_deg: Type.Number(),
    max_cm: Type.Number(),
    /** `null` means nothing was seen that way. */
    r_cm: Type.Array(Nullable(Type.Number())),
    age_s: Type.Optional(Nullable(Type.Number())),
    stale: Type.Optional(Type.Boolean()),
  },
  open,
);
export type ScanRingBody = Static<typeof ScanRingSchema>;

export const HeardItem = Type.Object(
  {
    text: Type.String(),
    kind: Type.Union([Type.Literal("speech"), Type.Literal("stop")]),
    age_s: Type.Number(),
  },
  open,
);
export type HeardItem = Static<typeof HeardItem>;

/** GET /telemetry/voice: what the robot heard (mcp_server.py `voice_json`). */
export const VoiceTelemetrySchema = Type.Object(
  {
    running: Type.Boolean(),
    error: Nullable(Type.String()),
    /** False means push-to-talk: silence usually means nobody held the button. */
    always_listening: Type.Boolean(),
    audio_source: Type.String(),
    mic_ever_open: Type.Boolean(),
    seconds_since_audio: Nullable(Type.Number()),
    utterances: Type.Number(),
    recent: Type.Array(HeardItem),
  },
  open,
);
export type VoiceTelemetry = Static<typeof VoiceTelemetrySchema>;

/** GET /telemetry/gate: the Nav2 cmd_vel gate (perception_link `status()` + `link`). */
export const GateStatusSchema = Type.Object(
  {
    enabled: Type.Boolean(),
    disabled_reason: Nullable(Type.String()),
    arm_expires_in_s: Nullable(Type.Number()),
    cmd_vel_received: Type.Number(),
    dropped_while_disabled: Type.Number(),
    clamped: Type.Number(),
    /** [vx, vy, wz] of the last command forwarded to the robot. */
    last_sent: Nullable(Type.Array(Type.Number())),
    last_sent_age_s: Nullable(Type.Number()),
    /** Null means no planner is publishing at all. */
    cmd_vel_age_s: Nullable(Type.Number()),
    clamps: Type.Optional(
      Type.Record(Type.String(), Type.Array(Type.Number())),
    ),
    link: Type.Optional(
      Type.Object(
        {
          started: Type.Optional(Nullable(Type.Boolean())),
          domain_id: Type.Optional(Nullable(Type.Number())),
          reports_received: Type.Optional(Nullable(Type.Number())),
        },
        open,
      ),
    ),
  },
  open,
);
export type GateStatus = Static<typeof GateStatusSchema>;

export const FaceRole = Type.Union([
  Type.Literal("superadmin"),
  Type.Literal("operator"),
]);
export type FaceRole = Static<typeof FaceRole>;

export const FaceSettings = Type.Object(
  {
    greet: Type.Boolean(),
    greet_unknown: Type.Boolean(),
    gate: Type.Boolean(),
    /** Robot mic on after greeting an enrolled person; read by apps/back. */
    listen_after_greet: Type.Boolean(),
  },
  open,
);
export type FaceSettings = Static<typeof FaceSettings>;

/** face_gate.py `public()`: a person without their features. */
export const FacePerson = Type.Object(
  {
    id: Type.String(),
    name: Type.String(),
    role: FaceRole,
    /** ISO text or epoch seconds, by when the person was enrolled. */
    enrolled_at: Nullable(Type.Union([Type.String(), Type.Number()])),
    has_photo: Type.Boolean(),
  },
  open,
);
export type FacePerson = Static<typeof FacePerson>;

/** GET /face/people (mcp_server.py `face_people`). */
export const FacesViewSchema = Type.Object(
  {
    settings: FaceSettings,
    people: Type.Array(FacePerson),
    unknown_seen: Type.Object(
      { count: Type.Number(), last_age_s: Nullable(Type.Number()) },
      open,
    ),
    models: Type.Boolean(),
  },
  open,
);
export type FacesView = Static<typeof FacesViewSchema>;
