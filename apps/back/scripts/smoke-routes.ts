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

const app = new Elysia().use(stateRoutes).use(skillsRoutes);

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

await show("GET /state         ", new Request("http://localhost/state"));
await show(
  "POST say (valid)   ",
  json("/skills/say/invoke", { text: "hi from route" }),
);
await show("POST say (bad body)", json("/skills/say/invoke", { text: 123 }));
await show("POST unknown skill ", json("/skills/nope/invoke", {}));

process.exit(0);
