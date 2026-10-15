# C3PO Web (`apps/web`)

SvelteKit 3 operator console (Svelte 5 runes, Tailwind CSS 4): login/signup and
a `(protected)` group with the dashboard, live map, cameras, agent chat with
history, voice control, VR control, a health board and settings. The page list
lives once, in `src/lib/nav.ts` — sidebar entries, topbar titles and browser-tab
titles all come from it. Talks to `apps/back` via Eden Treaty — the backend's `App`
type is imported from the `@repo/back` workspace package (its `exports` point
at `src/index.ts`; types only, nothing is bundled), so API calls like
`api.health.get()` are typed end to end.

## Develop

```bash
bun run dev          # vite dev on port 3001
bun run build        # production build
bun run preview      # preview the build (also `bun run start`)
bun run check-types  # svelte-check
bun test             # pure-logic tests in tests/ (see "UI conventions")
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
  If the backend is unreachable during that revalidation the request is
  treated as signed out **without clearing cookies**, and `locals.backendDown`
  lets `/login` say "No se pudo contactar al servidor" instead of every page
  500ing for a returning operator.
- `src/routes/(protected)/+layout.server.ts` redirects to
  `/login?redirectTo=<path>` without a session (and adds `&reason=expired` when
  `/state` answers 401); anything inside `(protected)/` is guarded
  automatically. A 401 seen later by any client poller does the same through
  `redirectToLogin()` — an expired session is never shown as a robot that is
  "offline". `redirectTo` is sanitised server-side (`login/auth-params.ts`,
  tested against open-redirect forms).
- Login/signup are server-rendered and redirect already-authenticated visitors
  in their `+page.server.ts`. Logout navigates with `refreshAll`, which is
  what keeps the root layout's `data.user` from bouncing `/login` →
  `/dashboard` → `/login`.
- Social buttons render only for providers `back` reports as configured on
  its public `GET /health` (`auth.providers`); with none, the forms show no
  social buttons at all.
- Server `load` functions fetching protected backend routes must forward the
  auth cookie:
  `api.route.get({ headers: { cookie: request.headers.get("cookie") ?? "" } })`.

## The console shell

Everything that must be true on every page lives in the `(protected)` layout,
which owns one instance each of `RobotLive` (`/state`), `VoiceLoopControl`
(`/voice/status`) and `Gate` (`/telemetry/gate`) and hands them down through
`#lib/robot/context.ts`.

- **Polling follows one policy** (`#lib/robot/poller.svelte.ts`): never two
  requests in flight, every request carries a timeout, a 401 goes to login,
  and optional pause while the tab is hidden. `/state` had no timeout once, and
  a bridge that stalled instead of refusing kept the topbar on "En línea" for
  the MCP SDK's 60 s default. Any new poller extends `Poller`. Eden Treaty
  never throws on a failed fetch — it returns `{status: 503, value: Error}` —
  so an `Error` value means "could not reach back", not a bridge 503.
- **One vocabulary for a missing link** (`#lib/robot/link-copy.ts`): "Sin
  conexión" (no answer), "Sin puente" (back says the bridge is down), "Sin
  acceso a la API", "Sesión vencida". "En vivo"/"Sin señal" are media words and
  never describe the robot link.
- **The e-stop is one press, wherever it is pressed.** Its state lives in
  `#lib/robot/estop.svelte.ts`, so the topbar button, the in-headset button on
  `/vr-control` (the only stop visible inside the WebXR dom-overlay — keep it)
  and the global **Shift+Space** hotkey always agree. Only telemetry read
  _after_ the 200 can confirm a stop, and only a posture the bridge can vouch
  for: offline, unknown (the post-reboot race), stale (`stale_fsm_*`) or stub
  all stay "Sin confirmar"; only "Detenido" clears itself. A 504
  `bridge_timeout` means back stopped waiting while the stop kept running on the
  bridge — "Sin respuesta", never "the order did not leave". The hotkey is
  caught in the capture phase so the focused control (a hold-to-walk button, a
  hold-to-confirm tile) never sees it, and `/vr-control` latches its own walk
  loop on any press. The topbar stop sits above every bits-ui overlay (sheet,
  dialog, alert dialog) — the stop is idempotent, so bypassing a modal is
  safe, and a modal that swallows the first tap on PARAR is not.
- **Privacy and autonomy are visible everywhere**: an "Escuchando" chip while a
  voice session runs, and "NAV ARMADO · N s" while Nav2 is armed to drive. An
  armed gate that can no longer be read keeps its chip and says "sin datos" —
  unknown is not closed.
- **Skill results have one meaning** (`#lib/robot/skill-outcome.ts`). The
  bridge never raises: a firmware refusal is a 200 with `status: "failed"`, a
  bring-up step can be acked without happening, a stub only logs. Every
  surface — chat tool cards, VR tiles and walk loop, the stop — classifies
  results through it.

## The live camera

`/live-camera` has two clients because the sim and the robot serve video two
different ways, and the page picks by env — `PUBLIC_ROBOT_CAM_URL` first, then
`PUBLIC_SIM_CAM_HOST`:

|           | Simulator                              | Real G1                                                                                       |
| --------- | -------------------------------------- | --------------------------------------------------------------------------------------------- |
| Server    | teleimager/aiortc, one per camera      | the bridge's `/camera` relay (videohub or the vision container), relayed again by `apps/back` |
| Transport | WebRTC (H.264, VP8 retry) over HTTPS   | MJPEG over HTTP from `apps/back` on 3000, behind Better Auth                                  |
| Client    | `src/lib/webrtc/sim-camera.ts`         | `src/lib/robot/mjpeg-camera.ts`                                                               |
| Cameras   | 3 (head stereo + two wrists)           | 1 — the D435i colour node is the only one fitted                                              |
| Bring-up  | ports 60001-3, accept the cert on each | `c3po up` on the robot; `PUBLIC_ROBOT_CAM_URL=http://localhost:3000/camera`                   |

Both modules carry the reasoning in their headers. The one thing worth
repeating here: an `<img>` on an MJPEG stream keeps showing the last frame it
received forever, so the robot client polls `/status` and the page renders
"congelado" over a picture that is real but old. Never remove that poll and
leave the picture — a frozen frame that reads as live is the failure this page
already tore out a fake HUD to avoid.

The robot picture is drawn by `src/lib/components/robot-camera-view.svelte`,
shared with `/vr-control` (which keeps owning the connection, because the XR
layer uses the same stream). The page shows `/camera/status`'s
`source`/`sources`/`hint`, and `/camera/frame.jpg` backs the Captura button. A
failed status fetch is classified as network vs CORS (`apps/back`'s `WEB_URL`;
`BRIDGE_CORS_ORIGINS` only when pointed straight at the bridge to debug) vs
mixed content (HTTPS console, HTTP camera), because they need different fixes
and used to share one "network" message.

## The live map

`/live-map` draws the robot's pose and travelled trail on a grid, with **Nav2's
global costmap** underneath it when one is available.

The costmap is the map, not FAST-LIO's point cloud. `/Laser_map` grows with
mapped area and is the one unbounded allocation in the stack; the costmap is
bounded by construction (a rolling 24 x 24 m window at 0.10 m), and it is what
Nav2 actually plans against — which is what you need when a path is refused or
loops. A 240 x 240 grid encodes to ~540 bytes of indexed PNG, so polling it at
1 Hz costs less than the `/state` JSON already being polled beside it.

**It takes the same route as the camera.** Both used to differ — the camera
went browser → bridge directly — and that asymmetry is gone:

|        | Live camera                                         | Live map                          |
| ------ | --------------------------------------------------- | --------------------------------- |
| Path   | browser → `back` `/camera` → bridge `:8001`         | browser → `back` → bridge `:8001` |
| Auth   | Better Auth, via `back`                             | Better Auth, via `back`           |
| Tunnel | `back` → bridge, the same 8001 tunnel `/mcp` uses   | the same                          |
| Env    | `PUBLIC_ROBOT_CAM_URL=http://localhost:3000/camera` | none — it uses `PUBLIC_API_URL`   |

The bridge's `:8001/camera` path selects and relays whichever onboard camera
producer currently owns the device, and `apps/back/src/routes/camera.ts` relays
that. The same bridge port also serves `/mcp`, the unauthenticated tool surface
that can walk the robot, which is why it listens on the robot's loopback only
and no browser is ever handed a route to it.

Two consequences worth knowing before you debug either:

- **The map can work while the camera does not.** They share the path up to the
  bridge, so when one works and the other does not the difference is on the
  robot: the camera needs a producer that owns `/dev/video4` (the bridge's
  `/camera/status` `hint` says which is missing), the map needs a nav2 stage.
- **"No map" has several distinct forms**, and they need different fixes: `sin
mapa` means nothing is publishing a costmap — almost always that no nav2 stage
  is running, and the bridge's hint naming the command is in the "¿Por qué?"
  disclosure under the status line; `mapa no disponible` means back or the
  bridge behind it did not answer (the disclosure says which); `mapa sin
metadatos de ubicación` means a map arrived that cannot be placed; and before
  the first answer the line reads `cargando mapa…`. A map already drawn is kept
  through later failures, desaturated and hatched — never faded, because fading
  erased the near-black lethal cells — with the failure and its age beside it
  (`mapa no disponible · último hace 42 s`); `desactualizado` is the bridge's own
  stale verdict. Showing an old map is fine, showing it as current is not.

The map and the dashboard's mini-map are the same component,
`pose-canvas.svelte`: one SVG in metres at a uniform scale, heading per REP-103
(yaw 0 = +x = right, positive counter-clockwise — the old wedge rotated the
wrong way), and an edge arrow with the distance when the robot is out of view.
The projection math lives in `src/lib/robot/map-geometry.ts`, with tests.

The costmap exists wherever Nav2 does, so `c3po perception up nav2-fake` brings it up
on synthetic odom and scan — **no sensors, nothing taken from the other team.**

## Voice, health and settings

- **`/voice`** is the voice session's home: the switch (an explicit decision —
  speech near the robot becomes turns), the engine's phase, who owns a
  realtime session (`ownedByYou`; only the owner can end it or open its
  transcript), what the robot heard, past voice transcripts, and an admin-only
  "Decir". Under the realtime engine the spoken stop phrase does nothing, and
  the page says so rather than showing a "Paradas 0" counter.
- **`/health`** is one row per subsystem — server, bridge and catalogue, robot
  link, battery, faults, autonomous navigation, perception, voice, camera,
  map — sorted worst first. The summary never says "todo en orden" while any
  row has no data. Classification is pure (`src/lib/robot/health.ts`, tested).
  Admins also get the command log (`GET /audit`).
- **`/settings`** shows the account and role, the console's environment and
  shortcuts, and — for admins — user management through Better Auth's admin
  plugin (no backend code; it gates itself). Roles reach an open console
  within ~5 minutes, because the role rides the session cookie cache;
  "Actualizar rol" forces a re-read. `ADMIN_EMAILS` re-grants admin at every
  backend restart.

## Deploy

`@sveltejs/adapter-vercel`, pinned to the `nodejs22.x` runtime (not edge)
because `getCookieCache` needs Node crypto — see the comment in
`vite.config.ts`.

## UI conventions

- `src/lib/components/ui/` — shadcn-svelte-style components (bits-ui +
  Tailwind); `src/lib/components/` — app-level components.
- Merge classes with `cn()` from `src/lib/utils.ts`.
- The design system is `src/routes/layout.css` — palette, tokens and the
  `panel` / `panel-hero` (one per screen) / `tile` / `eyebrow` / `readout` /
  `stamp` / `cta` utilities. No literal colours in components; cyan means
  "moving under power" and nothing else; 11 px (`text-2xs`) is the floor.
- Copy is Spanish in voseo. Bridge and agent text shown verbatim is wrapped in
  `<span lang="en">`. Section labels are `<h2 class="eyebrow">`.
- Inline `<Callout>` for a state that persists; a toast (`svelte-sonner`) for
  the outcome of a one-shot action.
- Decision logic goes in plain `.ts` modules with tests in `tests/` — `bun
test` cannot load a `.svelte.ts` (runes are a compiler feature), so the rune
  classes stay thin. `tests/bun-test.d.ts` is a deliberately small `bun:test`
  shim; widen it rather than adding `bun-types`.
- Use the Svelte MCP server when writing `.svelte` files (`list-sections` →
  `get-documentation`, then `svelte-autofixer`).
