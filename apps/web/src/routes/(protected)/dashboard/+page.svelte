<script lang="ts">
  import { goto } from "$app/navigation";
  import {
    ArrowRight,
    MapPin,
    ChevronRight,
    Map,
    ShieldCheck,
    TriangleAlert,
  } from "@lucide/svelte";
  import { Input } from "$lib/components/ui/input/index.js";
  import PostureFigure from "$lib/components/posture-figure.svelte";
  import { projectPose } from "$lib/robot/live-state.svelte";
  import { getRobotLive } from "$lib/robot/context";
  import {
    readPosture,
    LOAD_TEXT,
    LOAD_FILL,
    LOAD_BORDER,
  } from "$lib/robot/posture";

  let { data } = $props();

  // Shared with the map and the topbar — see `(protected)/+layout.svelte`.
  const live = getRobotLive();

  const robot = $derived(live.state);
  const online = $derived(live.online);
  const battery = $derived(
    robot?.battery_pct != null ? Math.round(robot.battery_pct) : null,
  );
  const faults = $derived(robot?.faults ?? []);
  const latencyMs = $derived(live.latencyMs ?? data.latencyMs);
  const pose = $derived(robot?.pose ?? null);
  const yawDeg = $derived(
    pose ? Math.round((pose.yaw_radians_world * 180) / Math.PI) : null,
  );
  const posture = $derived(readPosture(robot?.posture, online));

  const marker = $derived(
    pose
      ? projectPose(pose.x_meters_world, pose.y_meters_world)
      : { left: 50, top: 50 },
  );
  const trailPoints = $derived(
    live.trail
      .map((p) => {
        const { left, top } = projectPose(p.x, p.y);
        return `${left},${top}`;
      })
      .join(" "),
  );

  // Battery is the one number besides the posture that has a "running out"
  // dimension worth colouring.
  const batteryTone = $derived(
    battery == null
      ? "text-ink-mute"
      : battery <= 20
        ? "text-danger-soft"
        : battery <= 40
          ? "text-warn"
          : "text-ink",
  );

  let command = $state("");

  // The command box hands off to the agent chat, which streams Claude's reply
  // and tool calls; the query is auto-sent on arrival.
  function runCommand(e: SubmitEvent) {
    e.preventDefault();
    const text = command.trim();
    if (!text) return;
    goto(`/chat?q=${encodeURIComponent(text)}`);
  }
</script>

{#snippet fact(label: string, value: string, tone = "text-ink")}
  <!-- Values are never truncated. A coordinate clipped to "1.24, -0…" reads as
       a real reading rather than a hidden one, and the operator has no way to
       tell which. If a value outgrows its column it wraps, which is visibly a
       long value rather than invisibly a wrong one. -->
  <div class="flex min-w-0 flex-col gap-1 border-t border-hairline pt-2.5">
    <span class="eyebrow">{label}</span>
    <span class="font-mono text-sm break-words {tone}">{value}</span>
  </div>
{/snippet}

<!-- Two columns from 1400px: state on the left (what it is doing, then what you
     can say to it), the map on the right taking the full height. The map is the
     one panel that genuinely wants vertical room; giving the slack to the hero
     instead just left a tall empty band above the robot.

     The split is at 1400 rather than Tailwind's xl (1280) because at 1280 —
     minus a 255px sidebar — the hero track was too narrow to hold the figure
     and the extended headline side by side, and the headline got clipped.

     minmax(0,…) rather than bare fr: a track sized `1.5fr` still refuses to go
     below its content's min-content width, which let the headline push the
     layout wider than the viewport. -->
<div
  class="grid gap-4 pb-2 min-[1400px]:h-full min-[1400px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] min-[1400px]:pb-0"
>
  <div class="flex min-w-0 flex-col gap-4">
    <!-- Hero: what the robot is doing, right now. Everything else on this page
         is subordinate to it, which is why it is the only panel with the lift. -->
    <section class="flex min-w-0 shrink-0 flex-col panel-hero p-5 sm:p-7">
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
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span class="stamp text-2xl text-ink sm:text-[30px]">BIPED-01</span>
            <span class="readout"
              >Unitree G1{robot?.env ? ` · ${robot.env}` : ""}</span
            >
          </div>

          <p
            class="mt-3 stamp text-3xl leading-[1.05] lg:text-[38px] {LOAD_TEXT[
              posture.load
            ]}"
          >
            {posture.label}
          </p>
          <p class="mt-2 text-sm leading-relaxed text-ink-mute">
            {posture.detail}
          </p>
          <!-- The firmware's own token, kept because whoever is debugging DDS
               needs to know which FSM index produced the label above. -->
          {#if robot?.posture}
            <span class="mt-1.5 readout">fsm · {robot.posture}</span>
          {/if}

          <div class="grid grid-cols-2 gap-x-6 gap-y-3 pt-6">
            {@render fact(
              "Batería",
              battery != null ? `${battery} %` : "—",
              batteryTone,
            )}
            {@render fact("Rumbo", yawDeg != null ? `${yawDeg}°` : "—")}
            {@render fact(
              "Posición",
              pose
                ? `x ${pose.x_meters_world.toFixed(2)}  y ${pose.y_meters_world.toFixed(2)} m`
                : "—",
            )}
            {@render fact("Red", online ? `${latencyMs} ms` : "sin enlace")}
          </div>
        </div>
      </div>
    </section>

    <!-- What you can do about it. Sits under the hero and takes the leftover
         height, so a long fault list has somewhere to go. -->
    <section class="flex min-h-0 flex-1 flex-col gap-4 panel p-5">
      <form onsubmit={runCommand} class="flex flex-col gap-2">
        <label for="cmd" class="eyebrow">Decile qué hacer</label>
        <div class="flex items-center gap-2 tile py-1 pr-1 pl-3">
          <Input
            id="cmd"
            bind:value={command}
            placeholder="Caminá 2 metros y saludá"
            class="h-9 w-full rounded-none border-0 bg-transparent p-0 font-mono text-xs text-ink shadow-none placeholder:text-ink-mute focus-visible:ring-0"
          />
          <button
            type="submit"
            aria-label="Enviar al agente"
            disabled={command.trim().length === 0}
            class="flex size-8 shrink-0 items-center justify-center rounded-md text-ink-mute transition-colors hover:bg-accent hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent"
          >
            <ArrowRight class="size-4" />
          </button>
        </div>
      </form>

      <div class="flex min-h-0 flex-1 flex-col gap-2">
        <span class="eyebrow">Diagnóstico</span>
        <div class="min-h-0 flex-1 overflow-y-auto">
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
                <li class="flex items-start gap-2 text-sm text-danger-soft">
                  <TriangleAlert class="mt-0.5 size-4 shrink-0" />
                  <span class="min-w-0 font-mono text-xs break-words"
                    >{fault}</span
                  >
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      </div>
    </section>
  </div>

  <!-- Where it is. The one panel that genuinely benefits from height, so it
       takes the full column. -->
  <section class="flex min-w-0 flex-col panel p-5">
    <div class="flex items-baseline justify-between gap-3">
      <span class="eyebrow">Última ubicación conocida</span>
      <a
        href="/live-map"
        class="flex items-center gap-1 text-xs text-ink-mute transition-colors hover:text-ink"
      >
        Mapa completo
        <ChevronRight class="size-3.5" />
      </a>
    </div>

    <div
      class="relative mt-3 min-h-[220px] flex-1 overflow-hidden rounded-lg border border-hairline bg-trench"
    >
      <!-- A reference grid, so an empty frame reads as ground rather than as a
             panel that failed to load. Origin lines are brighter: (0,0) is where
             the robot's odometry started, which is the only fixed landmark. -->
      <svg
        class="absolute inset-0 size-full"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <g stroke="var(--c3-hairline)" stroke-width="0.2">
          {#each [20, 40, 60, 80] as t (t)}
            <line x1="0" y1={t} x2="100" y2={t} />
            <line x1={t} y1="0" x2={t} y2="100" />
          {/each}
        </g>
        <g stroke="var(--c3-hairline-strong)" stroke-width="0.3">
          <line x1="0" y1="50" x2="100" y2="50" />
          <line x1="50" y1="0" x2="50" y2="100" />
        </g>
      </svg>

      {#if live.trail.length > 1}
        <svg
          class="absolute inset-0 size-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <polyline
            points={trailPoints}
            fill="none"
            stroke="var(--c3-peri)"
            stroke-opacity="0.5"
            stroke-width="0.5"
            stroke-dasharray="0.6 1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      {/if}
      <span class="absolute top-2.5 left-3 readout">
        {#if pose}
          x {pose.x_meters_world.toFixed(2)} · y {pose.y_meters_world.toFixed(
            2,
          )} m
        {:else}
          sin posición
        {/if}
      </span>
      <div
        class="absolute transition-[left,top] duration-500 ease-out"
        style="left:{marker.left}%;top:{marker.top}%"
      >
        <!-- Same load encoding as the hero figure and the full map. -->
        <span
          class="absolute -inset-2 rounded-full border opacity-50 {LOAD_BORDER[
            posture.load
          ]}"
        ></span>
        <span
          class="relative block size-3 rounded-full {LOAD_FILL[posture.load]}"
        ></span>
      </div>
    </div>

    <div class="mt-3 flex items-center gap-2.5 tile p-3">
      <MapPin class="size-4 shrink-0 text-ink-mute" />
      <span class="flex-1 text-sm text-ink">Distancia recorrida</span>
      <span class="font-mono text-sm text-peri">
        {(live.distanceM / 1000).toFixed(2)} km
      </span>
    </div>
  </section>
</div>
