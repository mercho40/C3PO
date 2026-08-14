import { Elysia } from "elysia";
import { cors } from "@elysiajs/cors";
import { skillsRoutes } from "@back/routes/skills";
import { tasksRoutes } from "@back/routes/tasks";
import { stateRoutes } from "@back/routes/state";
import { agentRoutes } from "@back/routes/agent";
import { env } from "@back/lib/env";
import { betterAuthPlugin } from "@back/lib/auth-plugin";

const app = new Elysia()
  .use(betterAuthPlugin)
  .use(
    cors({
      origin: env.WEB_URL,
      methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
      credentials: true,
      allowedHeaders: ["Content-Type", "Authorization"],
    }),
  )
  .get("/health", () => ({ status: "ok", timestamp: Date.now() }))
  // skillsRoutes guards itself (see its own docstring — it needs `user`
  // typed on /invoke, which means its `auth` resolver has to run inside its
  // own scope; nesting it in the outer guard below too would resolve the
  // session twice per request). Everything else here can read or move the
  // robot (or spend Anthropic tokens via /agent) — require a session.
  // /health stays open for monitoring.
  .use(skillsRoutes)
  .guard({ auth: true }, (app) =>
    app.use(tasksRoutes).use(stateRoutes).use(agentRoutes),
  )
  .listen(env.PORT);

console.log(
  `🦊 Elysia is running at ${app.server?.hostname}:${app.server?.port}`,
);
export type App = typeof app;
