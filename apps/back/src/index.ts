import { Elysia } from "elysia";
import { auth } from "@back/lib/auth";
import { cors } from "@elysiajs/cors";
import { skillsRoutes } from "@back/routes/skills";
import { tasksRoutes } from "@back/routes/tasks";
import { stateRoutes } from "@back/routes/state";
import { agentRoutes } from "@back/routes/agent";

// user middleware (compute user and session and pass to routes)
const betterAuth = new Elysia({ name: "better-auth" })
  .mount(auth.handler)
  .macro({
    auth: {
      async resolve({ status, request: { headers } }) {
        const session = await auth.api.getSession({
          headers,
        });

        if (!session) return status(401);

        return {
          user: session.user,
          session: session.session,
        };
      },
    },
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
  .use(skillsRoutes)
  .use(tasksRoutes)
  .use(stateRoutes)
  .use(agentRoutes)
  .listen(3000);

console.log(
  `🦊 Elysia is running at ${app.server?.hostname}:${app.server?.port}`,
);
export type App = typeof app;
