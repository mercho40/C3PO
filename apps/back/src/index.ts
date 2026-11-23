/**
 * The composition root: read the config, build the real dependencies, start
 * the background loops, listen. Everything else is `createApp` (app.ts), which
 * reads no environment and starts nothing — so a test can build the same app
 * over fakes.
 *
 * `loadConfig()` runs first and throws, naming what is missing, before any
 * module has used a value. (index.ts used to get that guarantee from whichever
 * unrelated module's import graph happened to load `lib/env.ts` first.)
 */

import { createApp, createServices } from "./app";
import { readStoppedAt, startStopWatch } from "./agent/stop-watch";
import { loadConfig } from "./config";
import { createRealDeps } from "./deps";
import { reconcileAdmins } from "./lib/admin-bootstrap";

const config = loadConfig();
const { db, ...deps } = createRealDeps(config);
const services = createServices(deps);

// Before listening: make sure the accounts that are supposed to be able to
// drive the robot actually can. Awaited so the first request after boot sees
// the reconciled roles, and never fatal — a database that is not up yet must
// not stop the server from starting.
await reconcileAdmins(db, config.adminEmails).catch((error: unknown) => {
  console.warn("[admin] could not reconcile admin roles:", error);
});

// A PARAR sent straight to the bridge (headset, stop phrase) ends agent turns too.
startStopWatch(services.turns, () => readStoppedAt(deps.http));

const app = createApp(deps, services).listen({
  port: config.port,
  hostname: config.host,
});

services.voice.startListenAfterGreet();

console.log(
  `🦊 Elysia is running at ${app.server?.hostname}:${app.server?.port}`,
);

export type { App } from "./app";
export type { RobotState } from "#back/routes/state.js";
