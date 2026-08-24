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
| Transport | WebRTC (H.264, VP8 retry) over HTTPS   | MJPEG over plain HTTP on the robot LAN           |
| Client    | `src/lib/webrtc/sim-camera.ts`         | `src/lib/robot/mjpeg-camera.ts`                  |
| Cameras   | 3 (head stereo + two wrists)           | 1 — the D435i colour node is the only one fitted |
| Bring-up  | ports 60001-3, accept the cert on each | `c3po up perception`, then use bridge `:8001`    |

Both modules carry the reasoning in their headers. The one thing worth
repeating here: an `<img>` on an MJPEG stream keeps showing the last frame it
received forever, so the robot client polls `/status` and the page renders
"congelado" over a picture that is real but old. Never remove that poll and
leave the picture — a frozen frame that reads as live is the failure this page
already tore out a fake HUD to avoid.

## The live map

`/live-map` draws the robot's pose and travelled trail on a grid, with **Nav2's
global costmap** underneath it when one is available.

The costmap is the map, not FAST-LIO's point cloud. `/Laser_map` grows with
mapped area and is the one unbounded allocation in the stack; the costmap is
bounded by construction (a rolling 24 x 24 m window at 0.10 m), and it is what
Nav2 actually plans against — which is what you need when a path is refused or
loops. A 240 x 240 grid encodes to ~540 bytes of indexed PNG, so polling it at
1 Hz costs less than the `/state` JSON already being polled beside it.

**It takes a different route from the camera, and that asymmetry is deliberate.**

|        | Live camera                     | Live map                          |
| ------ | ------------------------------- | --------------------------------- |
| Path   | browser → bridge `:8001/camera` | browser → `back` → bridge `:8001` |
| Auth   | none (trusted robot LAN)        | Better Auth, via `back`           |
| Tunnel | none for desktop development    | none                              |
| Env    | `PUBLIC_ROBOT_CAM_URL`          | none — it uses `PUBLIC_API_URL`   |

The bridge's `:8001/camera` path selects and relays whichever onboard camera
producer currently owns the device. The same port also serves `/mcp`, so the
robot Wi-Fi must be treated as a trusted network: the bridge has no authentication
of its own. The map remains proxied through `back` because its data already comes
from an MCP tool and belongs behind the app's authentication boundary.

Two consequences worth knowing before you debug either:

- **The map can work while the camera does not.** The map goes through `back`,
  while the browser fetches camera bytes from the robot directly. A bad robot
  hostname, CORS policy, or browser network path can therefore break only video.
- **"No map" has three distinct forms**, and they need different fixes: `sin
mapa` means nothing is publishing a costmap — almost always that no nav2 stage
  is running, and the bridge's hint naming the command sits in the tooltip;
  `mapa no disponible` means the console cannot reach `back`; and a map shown at
  reduced opacity with `desactualizado` is real but stale. Showing an old map is
  fine, showing it as current is not.

The costmap exists wherever Nav2 does, so `perception_up nav2-fake` brings it up
on synthetic odom and scan — **no sensors, nothing taken from the other team.**

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
