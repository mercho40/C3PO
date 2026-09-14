import { Elysia } from "elysia";
import { betterAuth } from "@back/lib/auth-plugin";
import { cors } from "@elysiajs/cors";
import { skillsRoutes } from "@back/routes/skills";
import { tasksRoutes } from "@back/routes/tasks";
import { stateRoutes } from "@back/routes/state";
import { mapRoutes } from "@back/routes/map";
import { telemetryRoutes } from "@back/routes/telemetry";
import { agentRoutes } from "@back/routes/agent";
import { chatsRoutes } from "@back/routes/chats";
import { voiceRoutes } from "@back/routes/voice";
import { reconcileAdmins } from "@back/lib/admin-bootstrap";
// Imported for its VALUES below, and for its import-time validation.
//
// That validation already ran before this line — `admin-bootstrap` imports
// `env` too — which is precisely the problem with relying on it: the
// guarantee that WEB_URL is set came from an unrelated module's import graph,
// and deleting the admin bootstrap from this file would have silently
// restored the failure `env.ts` was written to end (CORS pinned to the origin
// `"undefined"`, which is a string, and matches nothing).
import { env } from "@back/lib/env";

// Before listening: make sure the accounts that are supposed to be able to
// drive the robot actually can. Awaited so the first request after boot sees
// the reconciled roles, and never fatal — a database that is not up yet must
// not stop the server from starting.
await reconcileAdmins().catch((error: unknown) => {
  console.warn("[admin] could not reconcile admin roles:", error);
});

const app = new Elysia()
  .use(betterAuth)
  .use(
    cors({
      origin: env.WEB_URL,
      methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
      credentials: true,
      allowedHeaders: ["Content-Type", "Authorization"],
    }),
  )
  .get("/health", () => ({ status: "ok", timestamp: Date.now() }))
  // Everything below can read or move the robot (or use the shared TIC AI key
  // via /agent) — require a session. /health stays open for monitoring.
  .guard({ auth: true }, (app) =>
    app
      .use(skillsRoutes)
      .use(tasksRoutes)
      .use(stateRoutes)
      .use(mapRoutes)
      .use(telemetryRoutes)
      .use(agentRoutes)
      .use(chatsRoutes)
      .use(voiceRoutes),
  )
  .listen(env.PORT);

console.log(
  `🦊 Elysia is running at ${app.server?.hostname}:${app.server?.port}`,
);
export type App = typeof app;
