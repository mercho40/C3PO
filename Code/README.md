# M4

Full-stack TypeScript monorepo starter template.

## Stack

- **Runtime:** Bun
- **Monorepo:** Turborepo
- **Frontend:** SvelteKit 5, Tailwind CSS 4, shadcn-svelte
- **Backend:** Elysia
- **Auth:** Better Auth (email/password, Google, GitHub)
- **Database:** Drizzle ORM, PostgreSQL
- **API Client:** Eden Treaty (type-safe RPC)

## Setup

```bash
bun install
```

Copy environment files and fill in your values:

```bash
cp apps/back/.env.example apps/back/.env
cp apps/web/.env.example apps/web/.env
```

The `BETTER_AUTH_SECRET` must match in both apps. Generate one with:

```bash
openssl rand -base64 32
```

Run database migrations:

```bash
cd apps/back && bunx drizzle-kit migrate
```

## Development

```bash
bun run dev
```

This starts both apps:
- **Frontend:** http://localhost:3001
- **Backend:** http://localhost:3000

## Scripts

| Command | Description |
|---------|-------------|
| `bun run dev` | Start all apps in development |
| `bun run build` | Build all apps |
| `bun run check-types` | Type check across monorepo |
| `bun run format` | Format with Prettier |
| `bun run start` | Start production (requires build) |

### Backend (apps/back)

| Command | Description |
|---------|-------------|
| `bunx drizzle-kit generate` | Generate migrations |
| `bunx drizzle-kit migrate` | Apply migrations |
| `bunx drizzle-kit studio` | Open Drizzle Studio |

## Project Structure

```
apps/
  back/           Elysia API server (port 3000)
    src/
      index.ts        Server entry, routes, auth macro
      lib/auth.ts     Better Auth configuration
      db/schema.ts    Drizzle database schema
      db/drizzle.ts   Database client
  web/            SvelteKit frontend (port 3001)
    src/
      hooks.server.ts     Auth session from cookie cache
      lib/api.ts          Eden Treaty client
      lib/auth-client.ts  Better Auth client
      lib/components/     App components
      lib/components/ui/  shadcn-svelte components
      routes/
        (protected)/      Auth-guarded routes
        login/            Prerendered login page
        signup/           Prerendered signup page
```
