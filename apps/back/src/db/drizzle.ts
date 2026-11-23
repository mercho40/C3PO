import { drizzle } from "drizzle-orm/bun-sql";
import * as schema from "./schema";

/**
 * The Postgres handle, built by the composition root (index.ts) from
 * `config.databaseUrl`. Lazy: nothing connects until the first query.
 */
export function createDb(url: string) {
  return drizzle({
    connection: {
      url,
      // Neon closes idle connections (and suspends the compute) after a few
      // quiet minutes. Bun's pool kept handing out those dead connections, so
      // the first write after a lull failed with ERR_POSTGRES_CONNECTION_CLOSED
      // — and the audit-trail inserts, which never throw on purpose, silently
      // lost the row. Closing idle connections on our side first means a lull
      // costs a reconnect, not a lost write.
      idleTimeout: 30,
      maxLifetime: 30 * 60,
    },
    schema,
  });
}

export type Db = ReturnType<typeof createDb>;
