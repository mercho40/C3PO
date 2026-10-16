import { drizzle } from "drizzle-orm/bun-sql";
// import { drizzle } from 'drizzle-orm/node-postgres';
import * as schema from "./schema";
import { env } from "#back/lib/env.js";

export const db = drizzle({
  connection: {
    url: env.DATABASE_URL,
    // Neon closes idle connections (and suspends the compute) after a few
    // quiet minutes. Bun's pool kept handing out those dead connections, so the
    // first write after a lull failed with ERR_POSTGRES_CONNECTION_CLOSED — and
    // the audit-trail inserts, which never throw on purpose, silently lost the
    // row. Closing idle connections on our side first means a lull costs a
    // reconnect, not a lost write.
    idleTimeout: 30,
    maxLifetime: 30 * 60,
  },
  schema,
});
