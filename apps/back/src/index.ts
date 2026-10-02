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
import { auditRoutes } from "@back/routes/audit";
import { enabledSocialProviders } from "@back/lib/auth";
import { reconcileAdmins } from "@back/lib/admin-bootstrap";

// Before listening: make sure the accounts that are supposed to be able to
// drive the robot actually can. Awaited so the first request after boot sees
// the reconciled roles, and never fatal — a database that is not up yet must
// not stop the server from starting.
await reconcileAdmins().catch((error: unknown) => {
  console.warn("[admin] could not reconcile admin roles:", error);
});

const app = new Elysia()
  // An unexpected throw must not reach the browser verbatim: Elysia's default
  // answers with the error's message, and a failed drizzle query's message is
  // the full SQL (the command log's 500 showed its own query to the console).
  // Log it here; send a generic body. Validation, parse and not-found errors
  // keep Elysia's own answers — those are meant for the caller.
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
  .use(betterAuth)
  .use(
    cors({
      origin: process.env.WEB_URL!,
      methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
      credentials: true,
      allowedHeaders: ["Content-Type", "Authorization"],
    }),
  )
  // `auth.providers` lets the login page render only the social buttons that
  // have credentials (see `enabledSocialProviders`). Public on purpose: which
  // sign-in methods exist is not a secret, and the login page has no session.
  .get("/health", () => ({
    status: "ok" as const,
    timestamp: Date.now(),
    auth: { providers: enabledSocialProviders },
  }))
  // These two carry their OWN `.guard({ auth: true })` because they read a
  // typed `user` (the admin checks), so they sit beside the outer guard rather
  // than inside it. Nested, the auth macro's resolver runs twice per request —
  // measured: two `getSession()` calls on every PARAR and every ~1.1/s VR
  // walk_velocity. Unauthenticated requests still get 401 from their own guard
  // (see skills.ts / audit.ts and their tests).
  .use(skillsRoutes)
  .use(auditRoutes)
  // Everything below can read or move the robot (or use the shared TIC AI key
  // via /agent) — require a session. /health stays open for monitoring.
  .guard({ auth: true }, (app) =>
    app
      .use(tasksRoutes)
      .use(stateRoutes)
      .use(mapRoutes)
      .use(telemetryRoutes)
      .use(agentRoutes)
      .use(chatsRoutes)
      .use(voiceRoutes),
  )
  .listen(Number(process.env.PORT) || 3000);

console.log(
  `🦊 Elysia is running at ${app.server?.hostname}:${app.server?.port}`,
);
export type App = typeof app;
