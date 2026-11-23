/**
 * The real edges of the app: Postgres, Better Auth, the bridge's two clients.
 * Lazy — nothing here connects until the first request uses it — so index.ts
 * and the scripts that drive the agent build them the same way.
 */

import type { AppDeps } from "./app";
import { createBridgeClient } from "./bridge/client";
import { createBridgeHttp } from "./bridge/http";
import type { Config } from "./config";
import { createAuditReader } from "./db/audit";
import { createChatStore } from "./db/chats";
import { createDb, type Db } from "./db/drizzle";
import { createAuth } from "./lib/auth";
import { betterAuthSessions } from "./lib/auth-plugin";

export function createRealDeps(config: Config): AppDeps & { db: Db } {
  const db = createDb(config.databaseUrl);
  return {
    config,
    db,
    sessions: betterAuthSessions(createAuth(config, db)),
    chats: createChatStore(db),
    audit: createAuditReader(db),
    bridge: createBridgeClient(config.bridge.mcpUrl),
    http: createBridgeHttp(config.bridge),
  };
}
