import { drizzle } from 'drizzle-orm/bun-sql';
// import { drizzle } from 'drizzle-orm/node-postgres';
import * as schema from "./schema";

export const db = drizzle({
  connection: { url: process.env.DATABASE_URL! },
  schema,
});
