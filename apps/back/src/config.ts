/**
 * Every setting apps/back reads, read once, by `loadConfig()`, in index.ts.
 *
 * Modules used to read `process.env` wherever they needed a value: at import
 * (the agent's provider, the Realtime model, the listen-after-greet window,
 * `lib/env.ts`'s throw) or per call (AGENT_TOOLS, the confirm secret, the
 * teleop secret). Which value a module saw depended on WHEN it was first
 * imported, so tests ran config-dependent code in child processes or in
 * query-string copies of a module, and index.ts got its "WEB_URL is set"
 * guarantee from whichever unrelated module happened to import `env.ts` first.
 *
 * Now the composition root (index.ts) calls `loadConfig()` before it builds
 * anything, and every module gets the slice it needs as an argument. Tests
 * build a Config of their own; nothing reads the environment behind their back.
 * This is the only file under src/ that reads `process.env` (lib/env.test.ts
 * holds it to that).
 *
 * REQUIRED is checked here and fails fast, naming what is missing: an unset
 * variable used to become `undefined`, or the STRING "undefined" (CORS pinned
 * to the origin "undefined", a DB connecting to nowhere). GITHUB_/GOOGLE_
 * client creds are NOT required: unset means that OAuth provider is disabled,
 * which is a legitimate config, not a misconfiguration. Optional settings keep
 * the defaults (and the 0/NaN guards) they had where they used to be read.
 */

import { bridgeUrl } from "./bridge/url";
import {
  availableEngines,
  defaultEngine,
  type VoiceEngine,
} from "./voice/engine";

type Env = Record<string, string | undefined>;

const REQUIRED = [
  "DATABASE_URL",
  "WEB_URL",
  "BETTER_AUTH_URL",
  "BETTER_AUTH_SECRET",
] as const;

export type AgentConfig = {
  baseUrl: string;
  /** Unset is legal at boot: the /agent turn that needs it says so. */
  apiKey: string | null;
  model: string;
  /** For `look` / `verify`; the agent's own model unless VISION_MODEL. */
  visionModel: string;
  maxSteps: number;
  turnBudgetMs: number;
  /** AGENT_TOOLS: null offers every bridge tool. */
  tools: ReadonlySet<string> | null;
  /** The optional second provider, when all three of its variables are set. */
  fallback: { baseUrl: string; model: string; apiKey: string } | null;
  /** Some but not all of the fallback's variables are set (failover is off). */
  fallbackIncomplete: boolean;
  fallbackStickyMs: number;
};

export type TranscribeConfig = {
  baseUrl: string;
  apiKey: string | null;
  model: string;
  fallback: { baseUrl: string; model: string; key: string } | null;
  stickyMs: number;
};

export type Config = {
  port: number;
  /** Listen address (BACK_HOST). The IPv4 literal, not "localhost": that
   *  resolves to ::1 on macOS, and adb reverse (the Quest) dials IPv4 only.
   *  Not HOST: zsh sets $HOST to the machine name. 0.0.0.0 only on a LAN box
   *  (OPERATIONS.md §5). */
  host: string;
  webUrl: string;
  databaseUrl: string;
  auth: {
    url: string;
    secret: string;
    github: { clientId: string; clientSecret: string };
    google: { clientId: string; clientSecret: string };
  };
  /** ADMIN_EMAILS, lowercased; empty means nobody (lib/admin-bootstrap.ts). */
  adminEmails: string[];
  bridge: {
    /** The MCP endpoint; its HTTP routes are siblings (bridge/url.ts). */
    mcpUrl: string;
    /** SAFETY_CONFIRM_SECRET: the proof on privileged bridge calls. */
    confirmSecret: string | null;
  };
  /** TELEOP_SECRET: null mints no teleop token (routes/teleop.ts). */
  teleopSecret: string | null;
  agent: AgentConfig;
  transcribe: TranscribeConfig;
  realtime: { apiKey: string | null; model: string; voice: string };
  voice: {
    defaultEngine: VoiceEngine;
    availableEngines: VoiceEngine[];
    listenAfterGreetMs: number;
    /**
     * Whether the robot says the headset's agent replies out loud. Off unless
     * `HEADSET_SPEAK_REPLY=1`: the speaker is shared with gemm, and a robot
     * that starts talking after every push-to-talk is a change an operator
     * should opt into, not find out about at a demo.
     */
    headsetSpeaksReplies: boolean;
  };
};

/** `stepCountIs` compares with `===`, so 0 or NaN would mean no cap at all. */
function positive(raw: string | undefined, fallback: number): number {
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/** AGENT_TOOLS=a,b,c, trimmed; unset or empty is null (everything). */
export function parseToolAllowlist(
  raw: string | undefined,
): ReadonlySet<string> | null {
  const names = (raw ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return names.length ? new Set(names) : null;
}

export function loadConfig(env: Env = process.env): Config {
  const missing = REQUIRED.filter((key) => !env[key]);
  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variable(s): ${missing.join(", ")}. Check apps/back/.env against .env.example.`,
    );
  }

  const agentBaseUrl =
    env.AGENT_BASE_URL ?? "https://proyectos-tic.ort.edu.ar/c3po/api/v1";
  // `tic-chat` is the gateway's tool-calling chat model. Its site advertises
  // `tic-code` and `tic-embed` too, but on 2026-08-18 `GET /models` returned
  // only `tic-chat` and `tic-test` — trust the endpoint, not the brochure.
  const model = env.AGENT_MODEL ?? "tic-chat";
  const fbBase = env.AGENT_FALLBACK_BASE_URL;
  const fbModel = env.AGENT_FALLBACK_MODEL;
  const fbKey = env.AGENT_FALLBACK_API_KEY;
  const sticky = Number(env.AGENT_FALLBACK_STICKY_S);

  return {
    port: Number(env.PORT) || 3000,
    host: env.BACK_HOST || "127.0.0.1",
    webUrl: env.WEB_URL as string,
    databaseUrl: env.DATABASE_URL as string,
    auth: {
      url: env.BETTER_AUTH_URL as string,
      secret: env.BETTER_AUTH_SECRET as string,
      github: {
        clientId: env.GITHUB_CLIENT_ID ?? "",
        clientSecret: env.GITHUB_CLIENT_SECRET ?? "",
      },
      google: {
        clientId: env.GOOGLE_CLIENT_ID ?? "",
        clientSecret: env.GOOGLE_CLIENT_SECRET ?? "",
      },
    },
    adminEmails: (env.ADMIN_EMAILS ?? "")
      .split(",")
      .map((entry) => entry.trim().toLowerCase())
      .filter(Boolean),
    bridge: {
      mcpUrl: bridgeUrl(env.BRIDGE_URL),
      confirmSecret: env.SAFETY_CONFIRM_SECRET || null,
    },
    teleopSecret: env.TELEOP_SECRET || null,
    agent: {
      baseUrl: agentBaseUrl,
      apiKey: env.AGENT_API_KEY || null,
      model,
      visionModel: env.VISION_MODEL || model,
      // 40, not 12: plan -> act -> verify -> replan spends ~3 steps per
      // action, so 12 ran out on a two-object errand. The wall clock is the
      // real bound. An empty AGENT_MAX_STEPS= in a systemd EnvironmentFile
      // parses to 0, and a trailing comment to NaN: both mean the default.
      maxSteps: Number.isInteger(Number(env.AGENT_MAX_STEPS))
        ? positive(env.AGENT_MAX_STEPS, 40)
        : 40, // an integer > 0, or the default
      turnBudgetMs: positive(env.AGENT_TURN_BUDGET_S, 240) * 1000,
      tools: parseToolAllowlist(env.AGENT_TOOLS),
      fallback:
        fbBase && fbModel && fbKey
          ? { baseUrl: fbBase, model: fbModel, apiKey: fbKey }
          : null,
      fallbackIncomplete:
        !(fbBase && fbModel && fbKey) && Boolean(fbBase || fbModel || fbKey),
      fallbackStickyMs:
        (Number.isFinite(sticky) && sticky >= 0 ? sticky : 300) * 1000,
    },
    transcribe: {
      baseUrl: agentBaseUrl,
      apiKey: env.AGENT_API_KEY || null,
      model: env.AGENT_TRANSCRIBE_MODEL ?? "tic-transcript",
      fallback:
        fbBase && fbModel && fbKey
          ? { baseUrl: fbBase, model: fbModel, key: fbKey }
          : null,
      stickyMs: Number(env.AGENT_FALLBACK_STICKY_S ?? 300) * 1000,
    },
    realtime: {
      apiKey: env.OPENAI_API_KEY || null,
      model: env.OPENAI_REALTIME_MODEL ?? "gpt-realtime-2.1",
      voice: env.OPENAI_REALTIME_VOICE ?? "marin",
    },
    voice: {
      defaultEngine: defaultEngine(env),
      availableEngines: availableEngines(env),
      listenAfterGreetMs: (Number(env.C3PO_LISTEN_AFTER_GREET_S) || 20) * 1000,
      headsetSpeaksReplies: env.HEADSET_SPEAK_REPLY === "1",
    },
  };
}
