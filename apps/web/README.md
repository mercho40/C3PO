# C3PO Web (`apps/web`)

SvelteKit 5 operator console (Svelte runes, Tailwind CSS 4): login/signup and
a `(protected)` group with the dashboard, live map, live camera, and agent
chat with history. Talks to `apps/back` via Eden Treaty — the backend's `App`
type is imported through the `@back/*` alias (defined in `svelte.config.js`;
the generated `.svelte-kit/tsconfig.json` picks it up), so API calls like
`api.health.get()` are typed end to end.

## Develop

```bash
bun run dev          # vite dev on port 3001
bun run build        # production build
bun run preview      # preview the build
bun run check-types  # svelte-check
```

**Never add `--bun` to the dev script.** It forces Vite onto Bun's runtime,
where SvelteKit's `make_trackable` fails assigning an inspect symbol to
`URLSearchParams` — every page with a server `load` then 500s with
"Attempted to assign to readonly property". It also diverges dev from the
Node runtime production actually uses.

## Environment

`cp .env.example .env` — that file is the authority on the variables.
`BETTER_AUTH_SECRET` must match `apps/back`'s (see `apps/back/.env.example`);
`PUBLIC_SIM_CAM_HOST` points the live-camera page at the sim's teleimager
camera servers, and `PUBLIC_ROBOT_CAM_URL` points it at the real G1's — those
are two different transports, not two addresses for one (below).

## Auth flow

- `hooks.server.ts` reads the session from Better Auth's signed cookie cache
  (`getCookieCache`, no backend call on the fast path; the code comments
  cover the cache-lapse revalidation) and populates `event.locals.user` /
  `event.locals.session`.
- `src/routes/(protected)/+layout.server.ts` redirects to `/login` without a
  session; anything inside `(protected)/` is guarded automatically.
- Login/signup pages are server-rendered (not prerendered) and send
  already-authenticated visitors to `/dashboard` client-side — the root
  layout's `data.user` plus an `$effect` calling `goto`.
- Server `load` functions fetching protected backend routes must forward the
  auth cookie:
  `api.route.get({ headers: { cookie: request.headers.get("cookie") ?? "" } })`.

## The live camera

`/live-camera` has two clients because the sim and the robot serve video two
different ways, and the page picks by env — `PUBLIC_ROBOT_CAM_URL` first, then
`PUBLIC_SIM_CAM_HOST`:

|           | Simulator                              | Real G1                                          |
| --------- | -------------------------------------- | ------------------------------------------------ |
| Server    | teleimager/aiortc, one per camera      | `apps/perception`'s vision container             |
| Transport | WebRTC (H.264, VP8 retry) over HTTPS   | MJPEG over plain HTTP, through the SSH tunnel    |
| Client    | `src/lib/webrtc/sim-camera.ts`         | `src/lib/robot/mjpeg-camera.ts`                  |
| Cameras   | 3 (head stereo + two wrists)           | 1 — the D435i colour node is the only one fitted |
| Bring-up  | ports 60001-3, accept the cert on each | `perception_up perception`, forward :8081        |

Both modules carry the reasoning in their headers. The one thing worth
repeating here: an `<img>` on an MJPEG stream keeps showing the last frame it
received forever, so the robot client polls `/status` and the page renders
"congelado" over a picture that is real but old. Never remove that poll and
leave the picture — a frozen frame that reads as live is the failure this page
already tore out a fake HUD to avoid.

## Deploy

`@sveltejs/adapter-vercel`, pinned to the `nodejs22.x` runtime (not edge)
because `getCookieCache` needs Node crypto — see the comment in
`svelte.config.js`.

## UI conventions

- `src/lib/components/ui/` — shadcn-svelte-style components (bits-ui +
  Tailwind); `src/lib/components/` — app-level components.
- Merge classes with `cn()` from `src/lib/utils.ts`.
- Use the Svelte MCP server when writing `.svelte` files (`list-sections` →
  `get-documentation`, then `svelte-autofixer`).
