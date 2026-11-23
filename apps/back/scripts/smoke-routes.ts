/**
 * Route-level smoke test for the back↔bridge link (needs the bridge running in
 * HTTP mode — `bun run serve:http` in apps/bridge). Builds the real app over
 * the real bridge clients, with a fixed admin session and an audit trail that
 * goes to stdout, and drives it via `.handle()` — no DB/auth/port boot.
 *
 *   bun apps/back/scripts/smoke-routes.ts
 */

import { createApp } from "../src/app";
import { createBridgeClient } from "../src/bridge/client";
import { createBridgeHttp } from "../src/bridge/http";
import { loadConfig } from "../src/config";
import type { AuditReader } from "../src/db/audit";
import type { ChatStore } from "../src/db/chats";

const config = loadConfig({
  DATABASE_URL: "postgres://unused.invalid/none",
  WEB_URL: "http://localhost:3001",
  BETTER_AUTH_URL: "http://localhost:3000",
  BETTER_AUTH_SECRET: "smoke-routes-not-a-secret-0000000000",
  ...process.env,
});
const refuse = async () => {
  throw new Error("smoke-routes has no database");
};
const app = createApp({
  config,
  sessions: {
    handler: () => new Response("no auth in smoke-routes", { status: 404 }),
    getSession: async () => ({
      user: { id: "smoke", email: "smoke@local", name: "smoke", role: "admin" },
      session: { id: "smoke" },
    }),
  },
  chats: {
    logToolCall: async (row) => console.log("  audit:", JSON.stringify(row)),
    ensureChat: refuse,
    appendMessage: refuse,
    listChats: refuse,
    getChatWithMessages: refuse,
    deleteChat: refuse,
    renameChat: refuse,
  } as ChatStore,
  audit: { read: refuse, cursorExists: refuse } as AuditReader,
  bridge: createBridgeClient(config.bridge.mcpUrl),
  http: createBridgeHttp(config.bridge),
});

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
