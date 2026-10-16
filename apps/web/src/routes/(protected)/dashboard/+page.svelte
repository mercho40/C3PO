<script lang="ts">
  import { resolve } from "$app/paths";
  import { ArrowRight, ChevronRight, MapPin } from "@lucide/svelte";
  import { Input } from "#lib/components/ui/input/index.js";
  import PostureFigure from "#lib/components/posture-figure.svelte";
  import PoseCanvas from "#lib/components/pose-canvas.svelte";
  import FaultList from "#lib/components/fault-list.svelte";
  import ScaleBar from "#lib/components/robot/scale-bar.svelte";
  import AwarenessPanel from "#lib/components/awareness-panel.svelte";
  import ActiveTasks from "#lib/components/robot/active-tasks.svelte";
  import { getRobotLive } from "#lib/robot/context.js";
  import { readPosture, fsmReadout, LOAD_TEXT } from "#lib/robot/posture.js";
  import { MotionTracker } from "#lib/robot/motion.js";
  import { staleness } from "#lib/robot/faults.js";
  import { LINK } from "#lib/robot/link-copy.js";
  import { fitView } from "#lib/robot/map-geometry.js";
  import {
    UNIT_ID,
    batteryPct,
    batteryTone,
    formatDistance,
    formatLatency,
    formatPose,
    yawDegrees,
  } from "#lib/robot/format.js";

  // Shared with the map and the topbar — see `(protected)/+layout.svelte`.
  const live = getRobotLive();

  const robot = $derived(live.state);
  const online = $derived(live.online);
  const simulated = $derived(live.simulated);
  // Whether ANY reading ever arrived. Offline with no reading is a different
  // screen from offline with an old one: there is no "last" anything to label,
  // and every "· última" over a "—" claimed a measurement that never existed.
  const hasReading = $derived(robot !== null);
  const pose = $derived(robot?.pose ?? null);
  const stale = $derived(staleness(robot?.faults));
  // Position is history when the link is down or odometry stopped arriving
  // (`stale_pose_*`): the bridge keeps reporting the last pose either way.
  const poseOld = $derived(!online || stale.pose !== null);

  // Motion is MEASURED from consecutive poses, not read off the FSM token: a
  // gait controller (FSM 500/501/801/802) stands perfectly still at zero
  // stick, and the console's one animation and its cyan are reserved for a
  // robot that is actually moving.
  const motionTracker = new MotionTracker();
  const motion = $derived(
    motionTracker.observe(pose, live.lastOkAt, stale.pose !== null),
  );
  const posture = $derived(
    readPosture(robot?.posture, online, {
      motion,
      staleS: stale.posture,
      stub: simulated,
      // Same word as the topbar for the same fault, and no "última lectura"
      // when nothing was ever read (posture.ts).
      problem: live.problem,
      hasReading,
    }),
  );
  const fsm = $derived(fsmReadout(robot?.posture, robot?.raw?.fsm_id));

  const battery = $derived(batteryPct(robot?.battery_pct));
  const yawDeg = $derived(yawDegrees(pose?.yaw_radians_world));

  // What the latency number actually measures: browser → back → the bridge's
  // get_state tool call, as timed by the client poll. Not "network" — and not
  // the SSR-side seed from the layout load, which timed a different path and
  // froze at page load; "—" until the first client poll says otherwise.
  const latency = $derived(
    online && live.lastOkAt !== null ? live.latencyMs : null,
  );
  const latencyTone = $derived(
    latency == null
      ? "text-ink-mute"
      : latency > 500
        ? "text-warn"
        : "text-ink",
  );
  const linkLabel = $derived(LINK[live.problem ?? "offline"]);

  // A fact that is not a current measurement says which kind it is, in words
  // — "· última" (history) or "· simulado" (the stub's fabrication) — and in
  // the muted ink. It used to say it with opacity-55 alone, which took the
  // 11 px labels down to ~3:1 and told a screen reader nothing at all.
  // Simulated wins over old: a fabricated value is the bigger caveat.
  const batteryMark = $derived(
    simulated ? "simulada" : !online && hasReading ? "última" : null,
  );
  const poseMark = $derived(
    simulated ? "simulada" : pose && poseOld ? "última" : null,
  );
  const yawMark = $derived(
    simulated ? "simulado" : pose && poseOld ? "último" : null,
  );

  // Distance is a MEASUREMENT only once a pose has been seen (the trail gets
  // its first point then). Before that "0.0 m" claimed the robot had been
  // watched standing still; it had not been watched at all.
  const travelled = $derived(
    live.trail.length > 0 ? formatDistance(live.distanceM) : "—",
  );

  // One chip treatment for every text overlay on the map: same fill, edge
  // and padding, so none of them reads as more (or less) official.
  const chip =
    "rounded-md border border-hairline bg-abyss/80 px-2 py-1 backdrop-blur-sm";

  // The mini-map frames the whole session: the trail, the robot and the
  // origin (the only fixed landmark), so the path walked is always on screen.
  // Square-fit, so it holds on any canvas aspect.
  const view = $derived(
    fitView(
      [
        { x: 0, y: 0 },
        ...live.trail,
        ...(pose ? [{ x: pose.x_meters_world, y: pose.y_meters_world }] : []),
      ],
      1,
      1,
      { minSpan: 6, marginM: 1.5 },
    ),
  );
  let ppm = $state(0);

  let command = $state("");

  // The command box hands off to the agent chat as a plain GET form to
  // /chat?q=… — SvelteKit routes it client-side like a link — and the chat
  // auto-sends the query on arrival. Only an empty command is stopped here.
  function runCommand(e: SubmitEvent) {
    if (!command.trim()) e.preventDefault();
  }
</script>

{#snippet fact(
  label: string,
  value: string,
  tone = "text-ink",
  mark: string | null = null,
)}
  <!-- Values are never truncated. A coordinate clipped to "1.24, -0…" reads as
       a real reading rather than a hidden one, and the operator has no way to
       tell which. If a value outgrows its column it wraps, which is visibly a
       long value rather than invisibly a wrong one. -->
  <div class="flex min-w-0 flex-col gap-1 border-t border-hairline pt-2.5">
    <span class="eyebrow">{label}{mark ? ` · ${mark}` : ""}</span>
    <span class="font-mono text-sm break-words {mark ? 'text-ink-mute' : tone}"
      >{value}</span
    >
  </div>
{/snippet}

<!-- Two columns from 1400px. Left: what it is doing, then what you can say to
     it. Right: where it is, then what it sees and hears — so the D7 notes
     ("detection is OFFLINE — this is not an empty scene") sit on the FIRST
     screen. They used to start exactly one viewport down, under a grid sized
     to the full scroll height, on every desktop wide enough for two columns.

     The split is at 1400 rather than Tailwind's xl (1280) because at 1280 —
     minus a 255px sidebar — the hero track was too narrow to hold the figure
     and the extended headline side by side, and the headline got clipped.

     minmax(0,…) rather than bare fr: a track sized `1.5fr` still refuses to go
     below its content's min-content width, which let the headline push the
     layout wider than the viewport. -->
<div
  class="grid gap-4 pb-2 min-[1400px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] min-[1400px]:items-start"
>
  <div class="flex min-w-0 flex-col gap-4">
    <!-- Hero: what the robot is doing, right now. Everything else on this page
         is subordinate to it, which is why it is the only panel with the lift. -->
    <section
      class="flex min-w-0 flex-col panel-hero p-5 sm:p-7"
      aria-labelledby="hero-unit"
    >
      <!-- Side by side only from lg up. Between the sidebar appearing (768px)
           and 1024px there is not enough room for a 245px figure and a 38px
           extended headline on one line, and the headline was the thing that
           got clipped. -->
      <div class="flex flex-1 flex-col gap-6 lg:flex-row lg:gap-7">
        <div
          class="mx-auto h-[220px] w-[195px] shrink-0 self-center lg:mx-0 lg:h-auto lg:min-h-[250px] lg:w-[275px] lg:self-stretch"
        >
          <PostureFigure pose={posture.pose} load={posture.load} />
        </div>

        <!-- Bottom-aligned: the figure stands on the floor of the panel, and the
             readout sits on the same line. The slack collects above as headroom
             over the robot, which is at least a true thing about the space it
             occupies — centring the text instead left a diagonal void. -->
        <div class="flex min-w-0 flex-1 flex-col lg:justify-end">
          {#if simulated}
            <!-- Stub mode returns a hard-coded 87 %, pose (0,0) and no faults
                 for a robot that does not exist. Said first and loudly, so
                 nothing below is read as a measurement. -->
            <p
              class="mb-3 inline-flex w-fit items-center gap-2 rounded-full border border-warn/40 bg-warn/10 px-3 py-1 font-mono text-xs tracking-[0.12em] text-warn uppercase"
            >
              Simulación · datos ficticios
            </p>
          {/if}
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 id="hero-unit" class="stamp text-2xl text-ink sm:text-[30px]">
              {UNIT_ID}
            </h2>
            <span class="readout"
              >Unitree G1{robot?.env ? ` · ${robot.env}` : ""}</span
            >
          </div>

          <!-- Only what changes when the STATE changes is live. The age of a
               stale reading ("hace 35 s") is outside the region: inside it,
               it was re-announced on every 2 s poll. -->
          <div aria-live="polite">
            <p
              class="mt-3 stamp text-3xl leading-[1.05] lg:text-[38px] {LOAD_TEXT[
                posture.load
              ]}"
            >
              {posture.label}
            </p>
            <p class="mt-2 text-sm leading-relaxed text-ink-mute">
              {posture.note}
            </p>
          </div>
          {#if posture.since}
            <span class="mt-1 readout">{posture.since}</span>
          {/if}
          <!-- The firmware's own token AND its index, because whoever is
               debugging DDS needs to know which FSM mode produced the label
               above — "run" alone cannot tell 801 from 802. -->
          {#if fsm}
            <span class="mt-1.5 readout">{fsm}</span>
          {/if}

          <div class="grid grid-cols-2 gap-x-6 gap-y-3 pt-6">
            {@render fact(
              "Batería",
              battery != null ? `${battery} %` : "—",
              batteryTone(robot?.battery_pct),
              battery != null ? batteryMark : null,
            )}
            {@render fact(
              "Rumbo",
              yawDeg != null ? `${yawDeg}°` : "—",
              "text-ink",
              yawMark,
            )}
            {@render fact("Posición", formatPose(pose), "text-ink", poseMark)}
            {@render fact(
              "Latencia del puente",
              online ? formatLatency(latency) : linkLabel,
              online ? latencyTone : "text-ink-mute",
            )}
          </div>
        </div>
      </div>
    </section>

    <!-- What you can do about it. -->
    <section class="flex flex-col gap-5 panel p-5">
      <form
        action={resolve("chat")}
        method="GET"
        onsubmit={runCommand}
        class="flex flex-col gap-2"
      >
        <label for="cmd" class="eyebrow">Decile qué hacer</label>
        <!-- The visible focus lives on the tile: the input inside is borderless
             by design, and without this, tabbing into the page's main control
             showed nothing at all. -->
        <div
          class="flex items-center gap-2 tile py-1 pr-1 pl-3 transition-colors focus-within:border-accent-edge focus-within:ring-2 focus-within:ring-cyan-hot/40"
        >
          <!-- text-base below md: iOS Safari zooms the page on focus for any
               input under 16px. -->
          <Input
            id="cmd"
            name="q"
            autocomplete="off"
            bind:value={command}
            placeholder="Caminá 2 metros y saludá"
            class="h-9 w-full rounded-none border-0 bg-transparent p-0 font-mono text-base text-ink shadow-none placeholder:text-ink-mute focus-visible:ring-0 md:text-xs dark:bg-transparent"
          />
          <button
            type="submit"
            aria-label="Enviar al agente"
            disabled={command.trim().length === 0}
            class="flex size-9 shrink-0 items-center justify-center rounded-md text-ink-mute transition-colors hover:bg-accent hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40 disabled:hover:bg-transparent"
          >
            <ArrowRight class="size-4" aria-hidden="true" />
          </button>
        </div>
      </form>

      <ActiveTasks />

      <div class="flex flex-col gap-2">
        <h2 class="eyebrow">Diagnóstico</h2>
        <FaultList
          faults={robot?.faults ?? []}
          {online}
          problem={live.problem}
          {simulated}
        />
      </div>
    </section>
  </div>

  <div class="flex min-w-0 flex-col gap-4">
    <!-- Where it is. -->
    <section class="flex min-w-0 flex-col panel p-5">
      <div class="flex items-baseline justify-between gap-3">
        <!-- "Última ubicación conocida" only when there IS one: with no pose
             ever received it named a location nobody knew. -->
        <h2 class="eyebrow">
          {pose && poseOld ? "Última ubicación conocida" : "Ubicación"}
        </h2>
        <a
          href={resolve("live-map")}
          class="flex min-h-8 items-center gap-1 rounded text-xs text-ink-mute transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
        >
          Mapa completo
          <ChevronRight class="size-3.5" aria-hidden="true" />
        </a>
      </div>

      <PoseCanvas
        class="mt-3 h-[260px] rounded-lg border border-hairline sm:h-[300px]"
        {pose}
        trail={live.trail}
        load={posture.load}
        center={view.center}
        span={view.span}
        stale={poseOld || simulated}
        animate
        bind:ppm
        ariaLabel={pose
          ? `Mini-mapa: ${UNIT_ID} en ${formatPose(pose)}${poseOld ? ", última posición conocida" : ""}`
          : `Mini-mapa: ${UNIT_ID} sin posición`}
      >
        <span
          class={[
            "absolute top-2.5 left-3 readout",
            chip,
            poseOld ? "text-ink-mute" : "text-ink-dim",
          ]}
        >
          {pose ? formatPose(pose) : "sin posición"}
          {#if pose && stale.pose !== null}
            · odometría detenida hace {Math.round(stale.pose)} s
          {/if}
        </span>
        {#if ppm > 0}
          <div class={["pointer-events-none absolute bottom-2.5 left-3", chip]}>
            <ScaleBar {ppm} maxPx={90} />
          </div>
        {/if}
      </PoseCanvas>

      <div class="mt-3 flex items-center gap-2.5 tile p-3">
        <MapPin class="size-4 shrink-0 text-ink-mute" aria-hidden="true" />
        <span class="flex-1 text-sm text-ink">Recorrido en esta sesión</span>
        <span
          class={[
            "font-mono text-sm",
            live.trail.length > 0 ? "text-peri" : "text-ink-mute",
          ]}>{travelled}</span
        >
      </div>
    </section>

    <!-- What the robot SEES and HEARS. On the first screen at every width: the
         D7 notes are sentences ("detection is OFFLINE — this is not an empty
         scene") and they are the part an operator must actually read. The
         voice switch moved to /voice; the Hears header links there. -->
    <AwarenessPanel />
  </div>
</div>
