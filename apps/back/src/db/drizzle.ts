import { drizzle } from "drizzle-orm/bun-sql";
// import { drizzle } from 'drizzle-orm/node-postgres';
import * as schema from "./schema";
import { env } from "@back/lib/env";

export const db = drizzle({
  connection: { url: env.DATABASE_URL },
  schema,
});
