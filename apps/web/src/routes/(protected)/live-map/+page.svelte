<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { Navigation, Plus, Minus, MapPin } from "@lucide/svelte";
  import * as ToggleGroup from "$lib/components/ui/toggle-group/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { RobotLive, projectPose } from "$lib/robot/live-state.svelte";

  let { data } = $props();

  // Seed once from the server load, then poll live.
  const live = untrack(() => new RobotLive(data.state, data.online));
  onMount(() => {
    live.start();
    return () => live.stop();
  });

  const layers = ["Satélite", "Oscuro", "Terreno"];
  let activeLayer = $state("Oscuro");

  // Zoom drives the metres→canvas projection scale, so +/- actually work.
  let scale = $state(6);
  const zoomIn = () => (scale = Math.min(24, scale * 1.3));
  const zoomOut = () => (scale = Math.max(2, scale / 1.3));

  const robot = $derived(live.state);
  const online = $derived(live.online);
  const pose = $derived(robot?.pose ?? null);
  const battery = $derived(Math.round(robot?.battery_pct ?? 0));
  const yawDeg = $derived(
    pose ? Math.round((pose.yaw_radians_world * 180) / Math.PI) : 0,
  );
  const marker = $derived(
    pose ? projectPose(pose.x_meters_world, pose.y_meters_world, scale) : { left: 50, top: 50 },
  );
  const trailPoints = $derived(
    live.trail
      .map((p) => {
        const { left, top } = projectPose(p.x, p.y, scale);
        return `${left},${top}`;
      })
      .join(" "),
  );

  // Single live unit fed by /state. Multi-robot fleet is future work.
  const unit = $derived({
    id: "BIPED-01",
    tag: online ? "En vivo" : "Sin señal",
    kind: robot ? `Unitree G1 · ${robot.env}` : "Unitree G1",
    posture: robot?.posture ?? "—",
    seen: online ? "activo ahora" : "sin conexión",
    battery,
    active: online,
  });
</script>

<div class="flex h-full gap-[18px] pb-2">
  <!-- Map -->
  <section class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828]">
    <!-- Layer toolbar -->
    <div class="flex items-center gap-2 border-b border-[rgba(180,210,255,0.08)] bg-[#06121c] px-[18px] py-3.5">
      <ToggleGroup.Root
        type="single"
        value={activeLayer}
        onValueChange={(v) => {
          if (v) activeLayer = v;
        }}
        spacing={2}
        class="gap-2"
      >
        {#each layers as layer (layer)}
          <ToggleGroup.Item
            value={layer}
            class="h-auto rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] px-3.5 py-1.5 font-mono text-[10px] tracking-[0.12em] text-[#eaf1ff] uppercase hover:bg-[rgba(180,210,255,0.06)] hover:text-[#eaf1ff] data-[state=on]:border-transparent data-[state=on]:bg-gradient-to-b data-[state=on]:from-[#c6dcff] data-[state=on]:to-[#9fc5ff] data-[state=on]:font-bold data-[state=on]:text-[#06121c] data-[state=on]:shadow-[0px_8px_28px_-10px_rgba(126,229,255,0.55)]"
          >
            {layer}
          </ToggleGroup.Item>
        {/each}
      </ToggleGroup.Root>
    </div>

    <!-- Map canvas -->
    <div class="relative min-h-0 flex-1 bg-[radial-gradient(ellipse_at_center,#0c1220,#04070d_80%)]">
      <!-- grid lines -->
      <svg class="absolute inset-0 size-full opacity-60" preserveAspectRatio="none" viewBox="0 0 861 794">
        <g stroke="rgba(180,210,255,0.05)" stroke-width="1">
          <line x1="0" y1="265" x2="861" y2="265" />
          <line x1="0" y1="530" x2="861" y2="530" />
          <line x1="287" y1="0" x2="287" y2="794" />
          <line x1="574" y1="0" x2="574" y2="794" />
        </g>
      </svg>

      <!-- travelled path (integrated from the live pose stream) -->
      {#if live.trail.length > 1}
        <svg class="absolute inset-0 size-full" preserveAspectRatio="none" viewBox="0 0 100 100">
          <polyline
            points={trailPoints}
            fill="none"
            stroke="#9fc5ff"
            stroke-opacity="0.5"
            stroke-width="0.5"
            stroke-dasharray="0.6 1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      {/if}

      <!-- Robot marker (BIPED-01), driven by live pose -->
      <div class="absolute transition-[left,top] duration-500 ease-out" style="left:{marker.left}%;top:{marker.top}%">
        <span class="absolute -inset-7 rounded-full bg-[rgba(159,197,255,0.14)] blur-[7px]"></span>
        <span class="absolute -inset-3.5 rounded-full border {online ? 'border-[#9ae5f8]' : 'border-[#ff4d6a]'} opacity-50"></span>
        <!-- heading wedge rotates with yaw -->
        <span
          class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-full origin-bottom"
          style="transform:translate(-50%,-100%) rotate({yawDeg}deg)"
        >
          <span class="block h-4 w-px bg-[#9ae5f8]"></span>
        </span>
        <span class="relative block size-4 rounded-full {online ? 'bg-[#9ae5f8]' : 'bg-[#ff4d6a]'} shadow-[0px_0px_24px_#7ee5ff,0px_0px_0px_3px_rgba(5,7,13,0.85)]"></span>
        <span class="absolute -top-7 left-1/2 -translate-x-1/2 rounded-md border border-[rgba(180,210,255,0.18)] bg-[rgba(5,7,13,0.85)] px-2 py-1 font-mono text-[10px] tracking-wide whitespace-nowrap text-[#eaf1ff] backdrop-blur-sm">{unit.id}</span>
      </div>

      <!-- Compass -->
      <div class="absolute top-5 right-5 flex size-[60px] flex-col items-center justify-center rounded-full border border-[rgba(180,210,255,0.18)] bg-[rgba(5,7,13,0.6)] backdrop-blur-sm">
        <Navigation class="size-3.5 text-[#7ee5ff] transition-transform duration-500" style="transform:rotate({yawDeg}deg)" />
        <span class="font-mono text-[10px] tracking-wide text-[#9fc5ff]">{yawDeg}°</span>
      </div>

      <!-- Coordinates / scale -->
      <div class="absolute bottom-5 left-5 flex flex-col gap-1">
        <span class="font-mono text-[10px] text-[#8a96ad]">
          {#if pose}
            x {pose.x_meters_world.toFixed(2)} · y {pose.y_meters_world.toFixed(2)} m
          {:else}
            sin posición
          {/if}
        </span>
        <span class="h-px w-20 bg-[#8a96ad]"></span>
        <span class="font-mono text-[10px] text-[#8a96ad]">{(100 / scale / 2).toFixed(1)} m</span>
      </div>

      <!-- Zoom -->
      <div class="absolute right-5 bottom-5 flex flex-col overflow-hidden rounded-lg border border-[rgba(180,210,255,0.18)] bg-[rgba(5,7,13,0.6)] backdrop-blur-sm">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Acercar"
          onclick={zoomIn}
          class="size-8 rounded-none text-[#eaf1ff] hover:bg-[rgba(180,210,255,0.06)] hover:text-[#eaf1ff]"
        >
          <Plus class="size-3" />
        </Button>
        <span class="h-px w-full bg-[rgba(180,210,255,0.08)]"></span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Alejar"
          onclick={zoomOut}
          class="size-8 rounded-none text-[#eaf1ff] hover:bg-[rgba(180,210,255,0.06)] hover:text-[#eaf1ff]"
        >
          <Minus class="size-3" />
        </Button>
      </div>
    </div>
  </section>

  <!-- Units panel -->
  <aside class="flex w-[340px] shrink-0 flex-col gap-2.5 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-3.5">
    <div
      class="flex flex-col gap-2.5 rounded-lg border p-3.5 {unit.active
        ? 'border-[rgba(159,197,255,0.3)] bg-[rgba(159,197,255,0.14)]'
        : 'border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)]'}"
    >
      <div class="flex items-center gap-2.5">
        <span class="flex items-center rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.04)] px-1.5 py-1">
          <span class="size-1.5 rounded-sm {unit.active ? 'bg-[#5ee7a1] shadow-[0px_0px_10px_rgba(94,231,161,0.6)]' : 'bg-[#ff4d6a] shadow-[0px_0px_10px_rgba(255,77,106,0.6)]'}"></span>
        </span>
        <span class="font-mono text-xs tracking-wide text-[#eaf1ff]">{unit.id}</span>
        <span class="ml-auto font-mono text-[9px] tracking-[0.09em] text-[#8a96ad] uppercase">{unit.tag}</span>
      </div>
      <span class="font-mono text-[10px] tracking-wide text-[#8a96ad]">{unit.kind}</span>
      <div class="flex items-center gap-2 text-[#8a96ad]">
        <MapPin class="size-3.5 shrink-0" />
        <span class="text-[11px] capitalize">{unit.posture} · {unit.seen}</span>
      </div>
      <div class="flex items-center gap-2">
        <div class="h-1 flex-1 overflow-hidden rounded-full bg-[rgba(180,210,255,0.08)]">
          <div
            class="h-full rounded-full bg-gradient-to-r from-[#4a7dd1] to-[#7ee5ff] shadow-[0px_0px_12px_rgba(126,229,255,0.55)] transition-[width] duration-500"
            style="width:{unit.battery}%"
          ></div>
        </div>
        <span class="font-mono text-[11px] text-[#eaf1ff]">{robot?.battery_pct != null ? `${unit.battery}%` : "—"}</span>
      </div>
    </div>
    <p class="mt-1 font-mono text-[9px] leading-relaxed tracking-wide text-[#8a96ad]">
      Posición en tiempo real desde <span class="text-[#9fc5ff]">/state</span>. El origen (0, 0) está
      en el centro; el rastro integra el recorrido a medida que el robot se mueve.
    </p>
  </aside>
</div>
