<script lang="ts">
  import { onMount } from "svelte";
  import {
    Navigation,
    Plus,
    Minus,
    ShieldCheck,
    TriangleAlert,
  } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import PostureFigure from "$lib/components/posture-figure.svelte";
  import { projectPose, projectMeters } from "$lib/robot/live-state.svelte";
  import { Costmap } from "$lib/robot/costmap.svelte";
  import { getRobotLive } from "$lib/robot/context";
  import {
    readPosture,
    LOAD_TEXT,
    LOAD_FILL,
    LOAD_BORDER,
  } from "$lib/robot/posture";

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

  // Zoom drives the metres→canvas projection scale, so +/- actually work.
  let scale = $state(6);
  const zoomIn = () => (scale = Math.min(24, scale * 1.3));
  const zoomOut = () => (scale = Math.max(2, scale / 1.3));

  // The costmap's rect in canvas %, using the UNCLAMPED projection — clamping
  // corners would squash the image and break its alignment with the marker.
  // `origin` is the grid's bottom-left, so the top edge is origin + extent.
  const mapRect = $derived.by(() => {
    const p = costmap.placement;
    if (!p) return null;
    const topLeft = projectMeters(p.originXM, p.originYM + p.heightM, scale);
    const bottomRight = projectMeters(p.originXM + p.widthM, p.originYM, scale);
    return {
      left: topLeft.left,
      top: topLeft.top,
      width: bottomRight.left - topLeft.left,
      height: bottomRight.top - topLeft.top,
    };
  });

  const robot = $derived(live.state);
  const faults = $derived(robot?.faults ?? []);
  const online = $derived(live.online);
  const pose = $derived(robot?.pose ?? null);
  const battery = $derived(Math.round(robot?.battery_pct ?? 0));
  const yawDeg = $derived(
    pose ? Math.round((pose.yaw_radians_world * 180) / Math.PI) : 0,
  );
  const marker = $derived(
    pose
      ? projectPose(pose.x_meters_world, pose.y_meters_world, scale)
      : { left: 50, top: 50 },
  );
  const trailPoints = $derived(
    live.trail
      .map((p) => {
        const { left, top } = projectPose(p.x, p.y, scale);
        return `${left},${top}`;
      })
      .join(" "),
  );

  const posture = $derived(readPosture(robot?.posture, online));

  // Single live unit fed by /state. Multi-robot fleet is future work.
  const unit = $derived({
    id: "BIPED-01",
    tag: online ? "En vivo" : "Sin señal",
    kind: robot ? `Unitree G1 · ${robot.env}` : "Unitree G1",
    active: online,
  });
</script>

<div class="flex h-full min-h-0 flex-col gap-4 pb-2 lg:flex-row">
  <!-- Map -->
  <section class="relative min-h-[380px] min-w-0 flex-1 overflow-hidden panel">
    <div
      class="absolute inset-0 bg-[radial-gradient(ellipse_at_center,var(--c3-panel),var(--c3-trench)_80%)]"
    >
      <!-- Nav2's costmap. UNDER the reference grid on purpose: the grid is how
           you read distance, and a map that hides it makes the view prettier
           and less useful. `image-rendering: pixelated` because a costmap cell
           IS the unit of knowledge — smoothing invents confidence between
           cells that Nav2 never had. -->
      {#if costmap.url && mapRect}
        <img
          src={costmap.url}
          alt="Nav2 global costmap"
          class="pointer-events-none absolute transition-opacity duration-300"
          class:opacity-40={costmap.stale}
          class:opacity-80={!costmap.stale}
          style="left:{mapRect.left}%;top:{mapRect.top}%;width:{mapRect.width}%;height:{mapRect.height}%;image-rendering:pixelated"
        />
      {/if}

      <!-- Reference grid, matching the dashboard's mini-map so the two read as
           the same surface at different sizes. The origin cross is brighter:
           (0,0) is where odometry started, the only fixed landmark there is. -->
      <svg
        class="absolute inset-0 size-full"
        preserveAspectRatio="none"
        viewBox="0 0 100 100"
        aria-hidden="true"
      >
        <g stroke="var(--c3-hairline)" stroke-width="0.2">
          {#each [10, 20, 30, 40, 60, 70, 80, 90] as t (t)}
            <line x1="0" y1={t} x2="100" y2={t} />
            <line x1={t} y1="0" x2={t} y2="100" />
          {/each}
        </g>
        <g stroke="var(--c3-hairline-strong)" stroke-width="0.35">
          <line x1="0" y1="50" x2="100" y2="50" />
          <line x1="50" y1="0" x2="50" y2="100" />
        </g>
      </svg>

      <!-- Travelled path, integrated from the live pose stream -->
      {#if live.trail.length > 1}
        <svg
          class="absolute inset-0 size-full"
          preserveAspectRatio="none"
          viewBox="0 0 100 100"
          aria-hidden="true"
        >
          <polyline
            points={trailPoints}
            fill="none"
            stroke="var(--c3-peri)"
            stroke-opacity="0.5"
            stroke-width="0.5"
            stroke-dasharray="0.6 1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      {/if}

      <!-- Robot marker (BIPED-01), driven by live pose -->
      <div
        class="absolute transition-[left,top] duration-500 ease-out"
        style="left:{marker.left}%;top:{marker.top}%"
      >
        <!-- The marker carries load, not connectivity. It used to go cyan and
             glow whenever the bridge answered, which lit it up for a robot lying
             limp on the floor — the one colour the console reserves for "moving
             under power". -->
        {#if posture.load === "moving"}
          <span class="absolute -inset-7 rounded-full bg-accent blur-[7px]"
          ></span>
        {/if}
        <span
          class="absolute -inset-3.5 rounded-full border opacity-50 {LOAD_BORDER[
            posture.load
          ]}"
        ></span>
        <!-- Heading wedge rotates with yaw -->
        <span
          class="absolute top-1/2 left-1/2 origin-bottom"
          style="transform:translate(-50%,-100%) rotate({yawDeg}deg)"
        >
          <span class="block h-4 w-px {LOAD_FILL[posture.load]}"></span>
        </span>
        <span
          class="relative block size-4 rounded-full {LOAD_FILL[
            posture.load
          ]} shadow-[0_0_0_3px_rgba(5,7,13,0.85)] {posture.load === 'moving'
            ? 'shadow-[0_0_24px_var(--c3-cyan-hot),0_0_0_3px_rgba(5,7,13,0.85)]'
            : ''}"
        ></span>
        <span
          class="absolute -top-7 left-1/2 -translate-x-1/2 rounded-md border border-hairline-strong bg-abyss/85 px-2 py-1 readout whitespace-nowrap text-ink backdrop-blur-sm"
          >{unit.id}</span
        >
      </div>

      <!-- Compass -->
      <div
        class="absolute top-5 right-5 flex size-[60px] flex-col items-center justify-center rounded-full border border-hairline-strong bg-abyss/60 backdrop-blur-sm"
      >
        <Navigation
          class="size-3.5 text-cyan-hot transition-transform duration-500"
          style="transform:rotate({yawDeg}deg)"
        />
        <span class="readout text-peri">{yawDeg}°</span>
      </div>

      <!-- Coordinates / scale bar -->
      <div class="absolute bottom-5 left-5 flex flex-col gap-1">
        <span class="readout">
          {#if pose}
            x {pose.x_meters_world.toFixed(2)} · y {pose.y_meters_world.toFixed(
              2,
            )} m
          {:else}
            sin posición
          {/if}
        </span>
        <span class="h-px w-20 bg-ink-mute"></span>
        <span class="readout">{(100 / scale / 2).toFixed(1)} m</span>
        <!-- Costmap state, in three distinguishable forms. "no map is being
             published" and "the console cannot reach the API" are different
             problems with different fixes, and a single "sin mapa" for both
             would send the operator to the wrong one. The bridge's own hint —
             which names the command that would produce a costmap — rides in
             the tooltip rather than the label, because it is English and long. -->
        <span class="readout" title={costmap.reason ?? undefined}>
          {#if costmap.url && costmap.placement}
            mapa {costmap.placement.resolutionM.toFixed(2)} m/celda{#if costmap.stale}
              · desactualizado{:else if costmap.ageS !== null}
              · {costmap.ageS.toFixed(1)} s{/if}
          {:else if costmap.unreachable}
            mapa no disponible
          {:else}
            sin mapa
          {/if}
        </span>
      </div>

      <!-- Zoom -->
      <div
        class="absolute right-5 bottom-5 flex flex-col overflow-hidden rounded-lg border border-hairline-strong bg-abyss/60 backdrop-blur-sm"
      >
        <Button
          variant="ghost"
          size="icon"
          aria-label="Acercar"
          onclick={zoomIn}
          class="size-8 rounded-none text-ink hover:bg-wash-hover hover:text-ink"
        >
          <Plus class="size-3.5" />
        </Button>
        <span class="h-px w-full bg-hairline"></span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Alejar"
          onclick={zoomOut}
          class="size-8 rounded-none text-ink hover:bg-wash-hover hover:text-ink"
        >
          <Minus class="size-3.5" />
        </Button>
      </div>
    </div>
  </section>

  <!-- The unit itself: same body, same vocabulary as the dashboard, so moving
       between the two pages never means re-learning what a state looks like. -->
  <aside class="flex shrink-0 flex-col gap-4 panel p-4 lg:w-[320px]">
    <div class="flex items-center gap-2.5">
      <span
        class="size-1.5 shrink-0 rounded-full {unit.active
          ? 'bg-ok shadow-[0_0_10px_rgba(94,231,161,0.6)]'
          : 'bg-danger shadow-[0_0_10px_rgba(255,77,106,0.6)]'}"
      ></span>
      <span class="stamp text-base text-ink">{unit.id}</span>
      <span class="ml-auto eyebrow">{unit.tag}</span>
    </div>

    <div class="flex items-end gap-3 tile p-3">
      <div class="h-[112px] w-[92px] shrink-0">
        <PostureFigure pose={posture.pose} load={posture.load} />
      </div>
      <div class="flex min-w-0 flex-1 flex-col gap-1 pb-1">
        <span class="eyebrow">Postura</span>
        <span class="truncate stamp-quiet text-lg {LOAD_TEXT[posture.load]}"
          >{posture.label}</span
        >
        <span class="truncate readout">{unit.kind}</span>
      </div>
    </div>

    <div class="flex flex-col gap-2">
      <div class="flex items-baseline justify-between gap-3">
        <span class="eyebrow">Batería</span>
        <span class="font-mono text-sm text-ink"
          >{robot?.battery_pct != null ? `${battery} %` : "—"}</span
        >
      </div>
      <div class="h-1 overflow-hidden rounded-full bg-hairline-strong">
        <div
          class="h-full rounded-full bg-gradient-to-r from-deep-blue to-cyan-hot transition-[width] duration-500"
          style="width:{battery}%"
        ></div>
      </div>
    </div>

    <!-- The numbers the canvas can only afford to print at 11px in its corners,
         at a size that reads from across the room. Scale is deliberately not
         repeated here — the scale bar bottom-left of the map is the better
         instrument for it, since it is measured against the map itself. -->
    <div class="flex flex-col">
      {#each [["Rumbo", pose ? `${yawDeg}°` : "—"], ["Posición", pose ? `x ${pose.x_meters_world.toFixed(2)}  y ${pose.y_meters_world.toFixed(2)} m` : "—"], ["Recorrido", `${(live.distanceM / 1000).toFixed(2)} km`]] as [label, value] (label)}
        <div
          class="flex items-baseline justify-between gap-3 border-t border-hairline py-2.5"
        >
          <span class="eyebrow">{label}</span>
          <span class="font-mono text-sm text-ink">{value}</span>
        </div>
      {/each}
    </div>

    <!-- Faults were visible on the dashboard and nowhere else, so an operator
         watching the robot move across the map — exactly when a fault matters —
         had no way to know one had been raised. -->
    <div class="flex flex-col gap-2 border-t border-hairline pt-3">
      <span class="eyebrow">Diagnóstico</span>
      {#if !online}
        <p class="text-sm text-ink-mute">Sin conexión con el robot.</p>
      {:else if faults.length === 0}
        <p class="flex items-center gap-2 text-sm text-ok">
          <ShieldCheck class="size-4 shrink-0" />
          Sin fallos
        </p>
      {:else}
        <ul class="flex flex-col gap-2">
          {#each faults as fault (fault)}
            <li class="flex items-start gap-2 text-danger-soft">
              <TriangleAlert class="mt-0.5 size-4 shrink-0" />
              <span class="min-w-0 font-mono text-xs break-words">{fault}</span>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <p class="readout leading-relaxed">
      Posición en tiempo real desde <span class="text-peri">/state</span>. El
      origen (0, 0) está en el centro; el rastro integra el recorrido a medida
      que el robot se mueve.
    </p>
  </aside>
</div>
