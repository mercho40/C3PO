/**
 * The app, from its dependencies. Nothing here reads the environment, opens a
 * socket or starts a timer: index.ts builds the real dependencies and listens;
 * a test builds fakes and calls `app.handle(request)` (or Eden's
 * `treaty(app)`), in process, with no database, bridge or port.
 *
 * Two layers:
 *
 *   `AppDeps`        the edges — config, sessions (Better Auth), the chat and
 *                    audit stores (Postgres), the bridge's MCP client and its
 *                    HTTP client, the language models. What a test fakes.
 *   `createServices` everything built on them, once per process: the
 *                    confirmation store, the turns, the catalogue, the one
 *                    dispatch and the one stop, the confirm service, the agent,
 *                    the voice stack. What a test usually keeps real.
 *
 * MODULE MAP
 *
 *   config.ts                 loadConfig(): every setting, read once
 *   app.ts                    this file
 *   index.ts                  real deps, background loops, listen
 *   lib/auth*.ts              Better Auth and the `auth: true` macro
 *   db/                       createDb, the chat store, the audit reader
 *   bridge/client.ts          MCP: callTool / listTools, one session
 *   bridge/http.ts            the bridge's HTTP routes, one timeout policy
 *   bridge/stop.ts            stopRobot / dispatchDirect: the one stop path
 *   skills/dispatch.ts        dispatchSkill: send a skill, audit it
 *   skills/invoke.ts          /invoke's gate, validation and grant
 *   skills/catalogue.ts       the catalogue derived from the bridge, cached
 *   agent/confirmations.ts    pending «Confirmar» cards, outcome subscribers
 *   agent/confirm-service.ts  Confirmar / Cancelar / the face confirm
 *   agent/turns.ts            agent turns in flight; PARAR aborts them
 *   agent/stop-watch.ts       a stop latched on the bridge aborts them too
 *   agent/runtime.ts          the agent
 *   voice/service.ts          the legacy loop, realtime, listen-after-greet
 *   routes/*.ts               HTTP surfaces: factories over the services above
 */

import { cors } from "@elysiajs/cors";
import { Elysia } from "elysia";

import { createConfirmService } from "./agent/confirm-service";
import { createConfirmations } from "./agent/confirmations";
import { bridgeCamera } from "./agent/look";
import {
  createAgentModels,
  createAgentRuntime,
  type AgentModels,
} from "./agent/runtime";
import { createTurns } from "./agent/turns";
import type { BridgeMcp } from "./bridge/client";
import type { BridgeHttp } from "./bridge/http";
import { createStop } from "./bridge/stop";
import type { Config } from "./config";
import type { AuditReader } from "./db/audit";
import type { ChatStore } from "./db/chats";
import { enabledSocialProviders } from "./lib/auth";
import { createAuthPlugin, type SessionSource } from "./lib/auth-plugin";
import { agentRoutes } from "./routes/agent";
import { auditRoutes } from "./routes/audit";
import { cameraRoutes } from "./routes/camera";
import { chatsRoutes } from "./routes/chats";
import { faceRoutes } from "./routes/face";
import { mapRoutes } from "./routes/map";
import { safetyRoutes } from "./routes/safety";
import { skillsRoutes } from "./routes/skills";
import { stateRoutes } from "./routes/state";
import { tasksRoutes } from "./routes/tasks";
import { telemetryRoutes } from "./routes/telemetry";
import { teleopRoutes } from "./routes/teleop";
import { voiceRoutes } from "./routes/voice";
import { createCatalogue } from "./skills/catalogue";
import { createDispatcher } from "./skills/dispatch";
import { createSkillInvoker } from "./skills/invoke";
import { createVoiceService } from "./voice/service";
import { createTranscriber, type Transcribe } from "./voice/transcribe";

export type AppDeps = {
  config: Config;
  sessions: SessionSource;
  chats: ChatStore;
  audit: AuditReader;
  bridge: BridgeMcp;
  http: BridgeHttp;
  /** Default: `createAgentModels(config.agent)`. */
  models?: AgentModels;
  /** Default: `createTranscriber(config.transcribe)`. */
  transcribe?: Transcribe;
  /** Tests shorten the stop's wait and the face step-back. */
  timings?: {
    stopTimeoutMs?: number;
    faceStepBack?: { timeoutMs: number; everyMs: number };
  };
};

export function createServices(deps: AppDeps) {
  const { config, bridge, http, chats } = deps;
  const logToolCall = chats.logToolCall;

  const confirmations = createConfirmations();
  const turns = createTurns(confirmations);
  const catalogue = createCatalogue(bridge);
  const dispatch = createDispatcher({ bridge, logToolCall });
  const stop = createStop({
    dispatch,
    turns,
    timeoutMs: deps.timings?.stopTimeoutMs,
  });
  const invoker = createSkillInvoker({ catalogue, stop, http, logToolCall });
  const confirm = createConfirmService({
    confirmations,
    invoker,
    http,
    // Un-audited, like the bridge's own lines: it says, it moves nothing.
    say: (text) => bridge.callTool("say", { text }),
    faceStepBack: deps.timings?.faceStepBack,
  });
  const models = deps.models ?? createAgentModels(config.agent);
  const agent = createAgentRuntime({
    config: config.agent,
    models,
    catalogue,
    bridge,
    camera: bridgeCamera(http),
    dispatch,
    logToolCall,
    confirmations,
    turns,
  });
  const voice = createVoiceService({
    config: config.voice,
    realtime: {
      config: config.realtime,
      catalogue,
      agent,
      dispatch,
      chats,
      logToolCall,
      confirmations,
      http,
    },
    agent,
    bridge,
    dispatch,
    http,
    chats,
    confirmations,
    confirm,
    turns,
    transcribe: deps.transcribe ?? createTranscriber(config.transcribe),
  });

  return {
    confirmations,
    turns,
    catalogue,
    dispatch,
    stop,
    invoker,
    confirm,
    agent,
    voice,
  };
}

export type Services = ReturnType<typeof createServices>;

export function createApp(deps: AppDeps, services = createServices(deps)) {
  const { config, http, chats, bridge } = deps;
  const s = services;
  const authPlugin = createAuthPlugin(deps.sessions);
  const logToolCall = chats.logToolCall;
  const providers = enabledSocialProviders(config.auth);

  return (
    new Elysia()
      // An unexpected throw must not reach the browser verbatim: Elysia's
      // default answers with the error's message, and a failed drizzle query's
      // message is the full SQL (the command log's 500 showed its own query to
      // the console). Log it here; send a generic body. Validation, parse and
      // not-found errors keep Elysia's own answers — those are meant for the
      // caller.
      .onError({ as: "global" }, ({ code, error, path }) => {
        // A thrown `status(401, …)` arrives here with a NUMERIC code: it is a
        // deliberate answer, not a crash, and must keep its status and body.
        if (
          typeof code === "number" ||
          code === "VALIDATION" ||
          code === "PARSE" ||
          code === "NOT_FOUND"
        ) {
          return;
        }
        console.error(`[back] ${code} on ${path}:`, error);
        return new Response(JSON.stringify({ error: "internal_error" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });
      })
      .use(authPlugin)
      .use(
        cors({
          origin: config.webUrl,
          methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
          credentials: true,
          allowedHeaders: ["Content-Type", "Authorization"],
        }),
      )
      // `auth.providers` lets the login page render only the social buttons
      // that have credentials (see `enabledSocialProviders`). Public on
      // purpose: which sign-in methods exist is not a secret, and the login
      // page has no session.
      .get("/health", () => ({
        status: "ok" as const,
        timestamp: Date.now(),
        auth: { providers },
        // "fallback" while ia-tic is failed over (agent/failover.ts).
        agent: { provider: s.agent.activeProvider() },
      }))
      // These carry their OWN `.guard({ auth: true })` because they read a
      // typed `user` (the admin checks), so they sit beside the outer guard
      // rather than inside it. Nested, the auth macro's resolver runs twice
      // per request — measured: two `getSession()` calls on every PARAR and
      // every ~1.1/s VR walk_velocity. Unauthenticated requests still get 401
      // from their own guard (see skills.ts / audit.ts and their tests).
      .use(
        skillsRoutes({
          authPlugin,
          catalogue: s.catalogue,
          invoker: s.invoker,
          confirm: s.confirm,
        }),
      )
      .use(auditRoutes({ authPlugin, audit: deps.audit }))
      .use(
        safetyRoutes({
          authPlugin,
          http,
          confirmations: s.confirmations,
          logToolCall,
        }),
      )
      .use(
        faceRoutes({
          authPlugin,
          http,
          confirmations: s.confirmations,
          logToolCall,
          listenWindow: s.voice.listenWindow,
        }),
      )
      .use(teleopRoutes({ authPlugin, http, secret: config.teleopSecret }))
      // Everything below can read or move the robot (or use the shared TIC AI
      // key via /agent) — require a session. /health stays open for
      // monitoring.
      .guard({ auth: true }, (app) =>
        app
          .use(tasksRoutes({ bridge, dispatch: s.dispatch, stop: s.stop }))
          .use(stateRoutes({ bridge }))
          .use(mapRoutes({ http }))
          .use(telemetryRoutes({ http }))
          .use(cameraRoutes({ http }))
          .use(
            agentRoutes({
              authPlugin,
              agent: s.agent,
              turns: s.turns,
              chats,
              dispatch: s.dispatch,
              headsetSpeaksReplies: config.voice.headsetSpeaksReplies,
            }),
          )
          .use(chatsRoutes({ authPlugin, chats }))
          .use(voiceRoutes({ authPlugin, voice: s.voice })),
      )
  );
}

export type App = ReturnType<typeof createApp>;
