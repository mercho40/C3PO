<script lang="ts">
  import { onMount } from "svelte";
  import {
    Crosshair,
    House,
    Info,
    Minus,
    Navigation2,
    Plus,
  } from "@lucide/svelte";
  import PostureFigure from "#lib/components/posture-figure.svelte";
  import PoseCanvas from "#lib/components/pose-canvas.svelte";
  import FaultList from "#lib/components/fault-list.svelte";
  import ScaleBar from "#lib/components/robot/scale-bar.svelte";
  import MapLegend from "#lib/components/robot/map-legend.svelte";
  import { Costmap } from "#lib/robot/costmap.svelte.js";
  import { getRobotLive } from "#lib/robot/context.js";
  import { readPosture, LOAD_TEXT } from "#lib/robot/posture.js";
  import { MotionTracker } from "#lib/robot/motion.js";
  import { staleness } from "#lib/robot/faults.js";
  import { LINK } from "#lib/robot/link-copy.js";
  import { describeCostmap } from "#lib/robot/map-copy.js";
  import {
    SPAN_MAX,
    SPAN_MIN,
    clampSpan,
    compassCssRotation,
    type XY,
  } from "#lib/robot/map-geometry.js";
  import {
    UNIT_ID,
    batteryLevel,
    batteryPct,
    batteryTone,
    formatDistance,
    formatPose,
    yawDegrees,
  } from "#lib/robot/format.js";

  // Shared with the dashboard and the topbar — see `(protected)/+layout.svelte`.
  // Reusing it is what lets the travelled path survive the trip from /dashboard.
  const live = getRobotLive();

  // Nav2's global costmap, polled as a PNG. Owned by this page rather than the
  // shared live-state: the dashboard's mini-map has no room to render it, and
  // polling it there would fetch a grid nothing draws.
  const costmap = new Costmap();
  // onMount, not $effect — matching (protected)/+layout.svelte's RobotLive.
  // This is mount lifecycle (a timer, a fetch loop, object URLs to revoke), not
  // a derivation; an $effect here would also re-run on unrelated dependency
  // changes and restart the poll.
  onMount(() => {
    costmap.start();
    return () => costmap.stop();
  });

  const robot = $derived(live.state);
  const online = $derived(live.online);
  // No reading ever ≠ an old reading: nothing to label "· última" (see the
  // dashboard, which follows the same rule).
  const hasReading = $derived(robot !== null);
  const pose = $derived(robot?.pose ?? null);
  const stale = $derived(staleness(robot?.faults));
  // The pose on screen is history when the link is down OR odometry froze.
  const poseOld = $derived(!online || stale.pose !== null);

  const motionTracker = new MotionTracker();
  const motion = $derived(
    motionTracker.observe(pose, live.lastOkAt, stale.pose !== null),
  );
  const posture = $derived(
    readPosture(robot?.posture, online, {
      motion,
      staleS: stale.posture,
      stub: live.simulated,
      problem: live.problem,
      hasReading,
    }),
  );

  const yawDeg = $derived(yawDegrees(pose?.yaw_radians_world));
  const battery = $derived(batteryPct(robot?.battery_pct));
  const batteryBar = $derived(
    {
      unknown: "bg-ink-mute",
      critical: "bg-danger",
      low: "bg-warn",
      ok: "bg-peri",
      full: "bg-peri",
    }[batteryLevel(robot?.battery_pct)],
  );

  // --- the view -----------------------------------------------------------
  //
  // Following is the default once there is a costmap: the costmap is a 24 m
  // window that rolls WITH the robot, so a view pinned to the odometry origin
  // watched the map slide off-screen as soon as the robot walked away.
  // `everLoaded` flips once, so this default is applied once and an operator
  // who turns follow off keeps it off.
  let following = $derived(costmap.everLoaded);
  let center = $state<XY>({ x: 0, y: 0 });
  let span = $state(16);
  let ppm = $state(0);

  const viewCenter = $derived(
    following && pose
      ? { x: pose.x_meters_world, y: pose.y_meters_world }
      : center,
  );

  function onviewchange(next: { center: XY; span: number }, cause: string) {
    span = next.span;
    if (cause === "pan") {
      center = next.center;
      following = false;
    } else if (!following) {
      // A zoom while following keeps the robot centred: it zooms on the
      // robot, not on wherever the pointer happened to be.
      center = next.center;
    }
  }

  function zoom(factor: number) {
    span = clampSpan(span * factor);
  }

  function recenterOrigin() {
    following = false;
    center = { x: 0, y: 0 };
  }

  function followRobot() {
    if (following) {
      // Turning follow OFF keeps the current view where it is.
      center = viewCenter;
      following = false;
    } else {
      following = true;
    }
  }

  const mapLine = $derived(
    describeCostmap({
      loaded: costmap.loaded,
      status: costmap.status,
      url: costmap.url,
      placement: costmap.placement,
      ageS: costmap.ageS,
      bridgeStale: costmap.bridgeStale,
      reason: costmap.reason,
      hint: costmap.hint,
    }),
  );
  const mapLineTone = $derived(
    { ok: "text-ink-dim", warn: "text-warn", mute: "text-ink-mute" }[
      mapLine.tone
    ],
  );

  const canvasCostmap = $derived(
    costmap.url && costmap.placement
      ? {
          url: costmap.url,
          placement: costmap.placement,
          stale: costmap.stale,
        }
      : null,
  );

  const linkLabel = $derived(
    online ? LINK.online : LINK[live.problem ?? "offline"],
  );

  // A value that is not a current measurement says which kind it is, in
  // words and in the muted ink — never by opacity alone, which took the
  // 11 px labels down to ~3:1 and told a screen reader nothing.
  const batteryMark = $derived(
    battery == null
      ? null
      : live.simulated
        ? "simulada"
        : !online && hasReading
          ? "última"
          : null,
  );
  const facts = $derived([
    {
      label: "Rumbo",
      value: yawDeg != null ? `${yawDeg}°` : "—",
      mark: live.simulated ? "simulado" : pose && poseOld ? "último" : null,
    },
    {
      label: "Posición",
      value: formatPose(pose),
      mark: live.simulated ? "simulada" : pose && poseOld ? "última" : null,
    },
    {
      // A measurement only once a pose has been seen: "0.0 m" before that
      // claimed the robot was watched standing still.
      label: "Recorrido en esta sesión",
      value: live.trail.length > 0 ? formatDistance(live.distanceM) : "—",
      mark: null,
    },
  ]);

  // One treatment for every text chip over the map — same fill, edge and
  // padding — so none reads as more (or less) official than the others.
  const chip =
    "rounded-md border border-hairline bg-abyss/80 px-2 py-1 backdrop-blur-sm";

  const mapSummary = $derived(
    pose
      ? `Mapa en vivo. ${UNIT_ID} en ${formatPose(pose)}, rumbo ${yawDeg}°, ${posture.label}${poseOld ? " (última posición conocida)" : ""}`
      : `Mapa en vivo. ${UNIT_ID} sin posición`,
  );
</script>

<div class="flex h-full min-h-0 flex-col gap-4 pb-2 lg:flex-row">
  <!-- @container: the controls go icon-only by the MAP's width, not the
       viewport's — at 1024 px the map is ~380 px wide beside the aside, as
       narrow as a phone's. -->
  <section
    class="@container relative min-h-[420px] min-w-0 flex-1 overflow-hidden panel"
    aria-label="Mapa"
  >
    <PoseCanvas
      class="absolute inset-0"
      {pose}
      trail={live.trail}
      load={posture.load}
      center={viewCenter}
      {span}
      stale={poseOld || live.simulated}
      label={UNIT_ID}
      animate={!following}
      costmap={canvasCostmap}
      interactive
      {onviewchange}
      onlocate={() => (following = true)}
      bind:ppm
      ariaLabel={mapSummary}
    >
      <!-- View controls, top-left. Follow is a toggle, so it says whether it
           is on (aria-pressed) instead of being a button that does something
           unannounced.

           Bounded on the right by the compass (64 px + its 16 px inset): the
           row used to run under it on phones and at 1024 px, and the compass
           took the taps meant for "Recentrar". On a narrow map the labels go
           to screen-reader-only text and the buttons become 40 px icons.
           The row itself ignores the pointer, so the gap between the buttons
           still pans the map. -->
      <div
        class="pointer-events-none absolute top-4 right-[5.5rem] left-4 flex flex-wrap items-start gap-2"
      >
        <button
          type="button"
          onclick={followRobot}
          disabled={!pose}
          aria-pressed={following}
          title="Seguir robot"
          class={[
            "pointer-events-auto inline-flex min-h-10 items-center gap-1.5 rounded-full border bg-abyss/80 px-3 text-xs backdrop-blur-sm transition-colors focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40 sm:min-h-8 @max-md:size-10 @max-md:justify-center @max-md:px-0",
            following
              ? "border-accent-edge text-ink"
              : "border-hairline-strong text-ink-mute hover:text-ink",
          ]}
        >
          <Crosshair class="size-3.5 shrink-0" aria-hidden="true" />
          <span class="@max-md:sr-only">Seguir robot</span>
        </button>
        <button
          type="button"
          onclick={recenterOrigin}
          title="Recentrar en origen"
          class="pointer-events-auto inline-flex min-h-10 items-center gap-1.5 rounded-full border border-hairline-strong bg-abyss/80 px-3 text-xs text-ink-mute backdrop-blur-sm transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none sm:min-h-8 @max-md:size-10 @max-md:justify-center @max-md:px-0"
        >
          <House class="size-3.5 shrink-0" aria-hidden="true" />
          <span class="@max-md:sr-only">Recentrar en origen</span>
        </button>
      </div>

      <!-- Heading, top-right. The needle is an UP-pointing icon turned by
           90° − yaw (map-geometry.ts): the old one used a top-right-pointing
           glyph turned by +yaw, which read 45° at yaw 0 and turned the wrong
           way. Peri, not cyan: a heading is data, not motion.

           pointer-events-none: it is a readout, not a control, and it used to
           eat taps aimed at the buttons beside it. An old heading is drawn
           dashed and muted rather than faded. -->
      <div
        class={[
          "pointer-events-none absolute top-4 right-4 flex size-[64px] flex-col items-center justify-center gap-0.5 rounded-full border bg-abyss/80 backdrop-blur-sm",
          poseOld
            ? "border-dashed border-hairline-strong"
            : "border-hairline-strong",
        ]}
        aria-hidden="true"
      >
        {#if pose}
          <Navigation2
            class={[
              "size-4 motion-safe:transition-transform motion-safe:duration-500",
              poseOld ? "text-ink-mute" : "text-peri",
            ]}
            style="transform: rotate({compassCssRotation(
              pose.yaw_radians_world,
            )}deg)"
          />
          <span class={["readout", poseOld ? "text-ink-mute" : "text-ink-dim"]}
            >{yawDeg}°</span
          >
        {:else}
          <span class="readout">—</span>
        {/if}
      </div>

      <!-- Bottom-left: where it is, how far a metre is, and what the map is. -->
      <div
        class="pointer-events-none absolute bottom-4 left-4 flex max-w-[calc(100%-6rem)] flex-col items-start gap-2"
      >
        <span
          class={["readout", chip, poseOld ? "text-ink-mute" : "text-ink-dim"]}
        >
          {formatPose(pose)}
          {#if pose && !online}
            <!-- The LINK word, as in the topbar: "sin señal" is camera/map
                 vocabulary and named a different fault. -->
            · {LINK[live.problem ?? "offline"].toLowerCase()} · última posición
          {:else if pose && stale.pose !== null}
            · odometría detenida hace {Math.round(stale.pose)} s
          {/if}
        </span>
        {#if ppm > 0}
          <!-- Only once measured: an empty chip before the first layout. -->
          <div class={chip}>
            <ScaleBar {ppm} />
          </div>
        {/if}
        <div
          class={["pointer-events-auto flex flex-col items-start gap-1", chip]}
        >
          <!-- Costmap state in its distinguishable forms (map-copy.ts). The
               explanation used to live in a hover tooltip, out of reach of
               touch and keyboard; it is a disclosure now.

               Only the state words are live. The resolution and age tick
               every poll, and inside the status region they re-announced the
               map once a second. -->
          <p class="readout {mapLineTone}">
            <span role="status">{mapLine.label}</span>{#if mapLine.meta}<span
                >{` · ${mapLine.meta}`}</span
              >{/if}
          </p>
          {#if mapLine.detail || mapLine.hint}
            <details class="group max-w-[46ch]">
              <summary
                class="inline-flex cursor-pointer list-none items-center gap-1 rounded readout text-ink-mute hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none [&::-webkit-details-marker]:hidden"
              >
                <Info class="size-3" aria-hidden="true" />
                <span class="group-open:hidden">¿Por qué?</span>
                <span class="hidden group-open:inline">Ocultar</span>
              </summary>
              <div class="mt-1 flex flex-col gap-1 text-xs leading-relaxed">
                {#if mapLine.detail}
                  <p class="text-ink-dim">{mapLine.detail}</p>
                {/if}
                {#if mapLine.hintEs}
                  <!-- A sentence we know: Spanish first, the bridge's own
                       words underneath (map-copy.ts). -->
                  <p class="text-ink-dim">{mapLine.hintEs}</p>
                {/if}
                {#if mapLine.hint}
                  <p class="readout break-words">
                    Puente: <span lang="en">{mapLine.hint}</span>
                  </p>
                {/if}
              </div>
            </details>
          {/if}
        </div>
      </div>

      <!-- Zoom. 40 px targets on touch; the canvas also takes wheel, pinch
           and +/− from the keyboard. The ring is inset: the group clips its
           corners (overflow-hidden), and an outer ring was cut to a sliver. -->
      <div
        class="absolute right-4 bottom-4 flex flex-col overflow-hidden rounded-lg border border-hairline-strong bg-abyss/70 backdrop-blur-sm"
      >
        <button
          type="button"
          aria-label="Acercar"
          onclick={() => zoom(1 / 1.3)}
          disabled={span <= SPAN_MIN}
          class="flex size-10 items-center justify-center text-ink transition-colors hover:bg-wash-hover focus-visible:bg-wash-hover focus-visible:ring-2 focus-visible:ring-cyan-hot/60 focus-visible:outline-none focus-visible:ring-inset disabled:opacity-40 sm:size-9"
        >
          <Plus class="size-4" aria-hidden="true" />
        </button>
        <span class="h-px w-full bg-hairline"></span>
        <button
          type="button"
          aria-label="Alejar"
          onclick={() => zoom(1.3)}
          disabled={span >= SPAN_MAX}
          class="flex size-10 items-center justify-center text-ink transition-colors hover:bg-wash-hover focus-visible:bg-wash-hover focus-visible:ring-2 focus-visible:ring-cyan-hot/60 focus-visible:outline-none focus-visible:ring-inset disabled:opacity-40 sm:size-9"
        >
          <Minus class="size-4" aria-hidden="true" />
        </button>
      </div>
    </PoseCanvas>
  </section>

  <!-- The unit itself: same body, same vocabulary as the dashboard, so moving
       between the two pages never means re-learning what a state looks like. -->
  <aside
    class="flex shrink-0 flex-col gap-4 panel p-4 lg:w-[320px] lg:overflow-y-auto"
  >
    <div class="flex items-center gap-2.5">
      <span
        class={[
          "size-1.5 shrink-0 rounded-full",
          online ? "bg-ok" : "bg-danger",
        ]}
        aria-hidden="true"
      ></span>
      <span class="stamp text-base text-ink">{UNIT_ID}</span>
      <!-- Not a live region: the topbar already announces the link, and two
           regions saying the same word read it twice. -->
      <span class="ml-auto eyebrow">{linkLabel}</span>
    </div>

    {#if live.simulated}
      <!-- Stub mode: pose (0,0), 87 % and no faults are fabricated. -->
      <p
        class="w-fit rounded-full border border-warn/40 bg-warn/10 px-2.5 py-0.5 font-mono text-2xs tracking-[0.12em] text-warn uppercase"
      >
        Simulación · datos ficticios
      </p>
    {/if}

    <div class="flex items-end gap-3 tile p-3">
      <div class="h-[112px] w-[92px] shrink-0">
        <PostureFigure pose={posture.pose} load={posture.load} />
      </div>
      <div class="flex min-w-0 flex-1 flex-col gap-1 pb-1">
        <h2 class="eyebrow">Postura</h2>
        <span
          class="stamp-quiet text-lg break-words {LOAD_TEXT[posture.load]}"
          aria-live="polite">{posture.label}</span
        >
        {#if !online || posture.stale || live.simulated}
          <span class="text-xs leading-snug text-ink-mute"
            >{posture.detail}</span
          >
        {/if}
        <span class="readout break-words"
          >Unitree G1{robot?.env ? ` · ${robot.env}` : ""}</span
        >
      </div>
    </div>

    <div class="flex flex-col gap-2">
      <div class="flex items-baseline justify-between gap-3">
        <h2 class="eyebrow">
          Batería{batteryMark ? ` · ${batteryMark}` : ""}
        </h2>
        <span
          class="font-mono text-sm {batteryMark
            ? 'text-ink-mute'
            : batteryTone(robot?.battery_pct)}"
          >{battery != null ? `${battery} %` : "—"}</span
        >
      </div>
      <div class="h-1 overflow-hidden rounded-full bg-hairline-strong">
        <div
          class="h-full rounded-full transition-[width] duration-500 {batteryMark
            ? 'bg-ink-mute'
            : batteryBar}"
          style:width="{battery ?? 0}%"
        ></div>
      </div>
    </div>

    <!-- The numbers the canvas can only afford to print at 11px in its corners,
         at a size that reads from across the room. Scale is deliberately not
         repeated here — the scale bar bottom-left of the map is the better
         instrument for it, since it is measured against the map itself. -->
    <dl class="flex flex-col">
      {#each facts as fact (fact.label)}
        <div
          class="flex items-baseline justify-between gap-3 border-t border-hairline py-2.5"
        >
          <dt class="eyebrow">
            {fact.label}{fact.mark ? ` · ${fact.mark}` : ""}
          </dt>
          <dd
            class={[
              "text-right font-mono text-sm",
              fact.mark || fact.value === "—" ? "text-ink-mute" : "text-ink",
            ]}
          >
            {fact.value}
          </dd>
        </div>
      {/each}
    </dl>

    <!-- Faults were visible on the dashboard and nowhere else, so an operator
         watching the robot move across the map — exactly when a fault matters —
         had no way to know one had been raised. -->
    <div class="flex flex-col gap-2 border-t border-hairline pt-3">
      <h2 class="eyebrow">Diagnóstico</h2>
      <FaultList
        faults={robot?.faults ?? []}
        {online}
        problem={live.problem}
        simulated={live.simulated}
        compact
      />
    </div>

    <div class="flex flex-col gap-2 border-t border-hairline pt-3">
      <h2 class="eyebrow">Leyenda</h2>
      <MapLegend costmap={canvasCostmap !== null} load={posture.load} />
    </div>

    <p class="readout leading-relaxed">
      Posición desde <span class="text-peri">/state</span>, en metros del marco
      de odometría: (0, 0) es donde arrancó la odometría, no un punto del
      edificio. El rastro integra lo recorrido en esta sesión de la consola.
    </p>
  </aside>
</div>
