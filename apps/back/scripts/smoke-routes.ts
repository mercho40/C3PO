/**
 * Route-level smoke test for the back↔bridge link (needs the bridge running in
 * HTTP mode — `bun run serve:http` in apps/bridge). Composes the route plugins
 * into a throwaway app and drives it via `.handle()` — no DB/auth/port boot.
 *
 *   bun apps/back/scripts/smoke-routes.ts
 */

import { Elysia } from "elysia";

import { stateRoutes } from "../src/routes/state";
import { skillsRoutes } from "../src/routes/skills";
import { tasksRoutes } from "../src/routes/tasks";

const app = new Elysia().use(stateRoutes).use(skillsRoutes).use(tasksRoutes);

async function show(label: string, req: Request) {
  const res = await app.handle(req);
  console.log(label, "->", res.status, await res.text());
}

const json = (path: string, payload: unknown) =>
  new Request(`http://localhost${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });

await show("GET  /state           ", new Request("http://localhost/state"));
await show(
  "POST say invoke       ",
  json("/skills/say/invoke", { text: "hi" }),
);
await show(
  "POST say dry-run      ",
  json("/skills/say/dry-run", { text: "hi" }),
);
await show(
  "POST say dry-run (bad)",
  json("/skills/say/dry-run", { text: 123 }),
);
await show("POST cancel (unknown) ", json("/tasks/tsk_nope/cancel", {}));
await show(
  "GET  /tasks           ",
  new Request("http://localhost/tasks?include_recent=true"),
);
await show("POST unknown invoke   ", json("/skills/nope/invoke", {}));

process.exit(0);
