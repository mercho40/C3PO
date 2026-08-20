import { Elysia } from "elysia";
import { betterAuth } from "@back/lib/auth-plugin";
import { cors } from "@elysiajs/cors";
import { skillsRoutes } from "@back/routes/skills";
import { tasksRoutes } from "@back/routes/tasks";
import { stateRoutes } from "@back/routes/state";
import { mapRoutes } from "@back/routes/map";
import { agentRoutes } from "@back/routes/agent";
import { chatsRoutes } from "@back/routes/chats";
import { reconcileAdmins } from "@back/lib/admin-bootstrap";

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
      origin: process.env.WEB_URL!,
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
      .use(agentRoutes)
      .use(chatsRoutes),
  )
  .listen(Number(process.env.PORT) || 3000);

console.log(
  `🦊 Elysia is running at ${app.server?.hostname}:${app.server?.port}`,
);
export type App = typeof app;
