<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { goto } from "$app/navigation";
  import CircularProgress from "$lib/components/circular-progress.svelte";
  import {
    Play,
    Square,
    Send,
    MapPin,
    Maximize2,
    ChevronRight,
    Map,
    Loader2,
  } from "@lucide/svelte";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Progress } from "$lib/components/ui/progress/index.js";
  import { createApi } from "$lib/api";
  import { RobotLive, projectPose } from "$lib/robot/live-state.svelte";

  let { data } = $props();

  // Seed once from the server load for an instant first paint, then poll live.
  const live = untrack(() => new RobotLive(data.state, data.online));
  onMount(() => {
    live.start();
    return () => live.stop();
  });

  const robot = $derived(live.state);
  const online = $derived(live.online);
  const battery = $derived(Math.round(robot?.battery_pct ?? 0));
  const faults = $derived(robot?.faults ?? []);
  const latencyMs = $derived(live.latencyMs ?? data.latencyMs);
  const pose = $derived(robot?.pose ?? null);
  const yawDeg = $derived(
    pose ? Math.round((pose.yaw_radians_world * 180) / Math.PI) : null,
  );
  const marker = $derived(
    pose ? projectPose(pose.x_meters_world, pose.y_meters_world) : { left: 50, top: 50 },
  );
  const trailPoints = $derived(
    live.trail
      .map((p) => {
        const { left, top } = projectPose(p.x, p.y);
        return `${left},${top}`;
      })
      .join(" "),
  );
  const distanceKm = $derived(live.distanceM / 1000);

  // Live telemetry — sourced from real /state, no fabricated numbers.
  const telemetry = $derived([
    { label: "Batería", value: robot?.battery_pct != null ? String(battery) : "—", unit: "%", pct: battery },
    {
      label: "Latencia de red",
      value: online ? String(latencyMs) : "—",
      unit: "ms",
      pct: Math.min(100, Math.round((latencyMs ?? 0) / 3)),
    },
    {
      label: "Rumbo (yaw)",
      value: yawDeg != null ? String(yawDeg) : "—",
      unit: "°",
      pct: yawDeg != null ? Math.round((((yawDeg % 360) + 360) % 360) / 3.6) : 0,
    },
  ]);

  let command = $state("");
  let stopping = $state(false);
  let stopMsg = $state<string | null>(null);

  async function emergencyStop() {
    if (stopping) return;
    stopping = true;
    stopMsg = null;
    try {
      const { error } = await createApi(fetch)
        .skills({ name: "stop_everything" })
        .invoke.post({});
      stopMsg = error ? "Error al detener" : "Movimiento detenido";
    } catch {
      stopMsg = "Error al detener";
    } finally {
      stopping = false;
      setTimeout(() => (stopMsg = null), 3500);
    }
  }

  // The command box hands off to the agent chat, which streams Claude's reply
  // and tool calls; the query is auto-sent on arrival.
  function runCommand(e: SubmitEvent) {
    e.preventDefault();
    const text = command.trim();
    if (!text) return;
    goto(`/chat?q=${encodeURIComponent(text)}`);
  }
</script>

{#snippet sectionLabel(text: string)}
  <span class="text-[10px] font-medium tracking-[0.18em] text-[#8a96ad] uppercase">{text}</span>
{/snippet}

{#snippet statTile(label: string, value: string)}
  <div class="flex flex-col gap-1.5 rounded-lg border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-3.5">
    <span class="font-mono text-[9px] tracking-[0.16em] text-[#8a96ad] uppercase">{label}</span>
    <span class="text-[15px] text-[#eaf1ff]">{value}</span>
  </div>
{/snippet}

<div class="flex flex-col gap-[18px] pb-2">
  <!-- Row 1 -->
  <div class="flex flex-col gap-[18px] xl:flex-row">
    <!-- Estado del sistema -->
    <section class="flex-1 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6">
      <h2 class="text-base font-bold tracking-[0.12em] text-[#8a96ad] uppercase">Estado del sistema</h2>
      <div class="mt-5 flex items-center gap-4">
        <span class="text-xl text-[#eaf1ff]">BIPED-01</span>
        <Badge
          variant="outline"
          class="gap-2 rounded-full px-3 py-1.5 font-mono text-[11px] tracking-[0.1em] uppercase {online
            ? 'border-[rgba(94,231,161,0.3)] bg-[rgba(94,231,161,0.08)] text-[#5ee7a1]'
            : 'border-[rgba(255,77,106,0.3)] bg-[rgba(255,77,106,0.08)] text-[#ff4d6a]'}"
        >
          <span
            class="size-1.5 rounded-sm {online
              ? 'bg-[#5ee7a1] shadow-[0px_0px_10px_rgba(94,231,161,0.6)]'
              : 'bg-[#ff4d6a] shadow-[0px_0px_10px_rgba(255,77,106,0.6)]'}"
          ></span>
          {online ? "Conectado" : "Offline"}
        </Badge>
      </div>
      <div class="mt-5 grid grid-cols-2 gap-2.5">
        {@render statTile("Red", online ? `${latencyMs} ms` : "—")}
        {@render statTile("Estado", faults.length ? `${faults.length} fallo(s)` : "OK")}
        {@render statTile("Modo", robot?.posture ?? "—")}
        {@render statTile("Entorno", robot?.env ?? "—")}
      </div>
    </section>

    <!-- Telemetría -->
    <section class="rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6 xl:w-[326px]">
      {@render sectionLabel("Telemetría")}
      <div class="mt-5 flex flex-col gap-4">
        {#each telemetry as m (m.label)}
          <div class="flex flex-col gap-2">
            <div class="flex items-center justify-between">
              <span class="font-heading text-[10px] tracking-[0.06em] text-[#8a96ad] uppercase">{m.label}</span>
              <span class="font-mono text-xs text-[#eaf1ff]">{m.value}<span class="text-[#8a96ad]"> {m.unit}</span></span>
            </div>
            <Progress value={m.pct} class="h-1 bg-[rgba(180,210,255,0.08)]" />
          </div>
        {/each}
      </div>
    </section>

    <!-- Núcleo de energía -->
    <section class="flex flex-col rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6 xl:w-[326px]">
      <div class="flex items-center justify-between">
        {@render sectionLabel("Núcleo de energía")}
        <span class="text-[10px] tracking-[0.18em] text-[#9fc5ff] uppercase">⚡ {battery > 20 ? "En uso" : "Bajo"}</span>
      </div>
      <div class="flex flex-1 items-center justify-center py-2">
        <CircularProgress percentage={battery} />
      </div>
      <div class="grid grid-cols-2 gap-2.5 border-t border-[rgba(180,210,255,0.08)] pt-3.5">
        <div class="flex flex-col gap-1">
          <span class="font-mono text-[9px] tracking-[0.16em] text-[#8a96ad] uppercase">Postura</span>
          <span class="text-[15px] text-[#eaf1ff] capitalize">{robot?.posture ?? "—"}</span>
        </div>
        <div class="flex flex-col gap-1">
          <span class="font-mono text-[9px] tracking-[0.16em] text-[#8a96ad] uppercase">Latencia</span>
          <span class="text-[15px] text-[#eaf1ff]">{online ? `${latencyMs} ms` : "—"}</span>
        </div>
      </div>
    </section>
  </div>

  <!-- Row 2 -->
  <div class="flex flex-col gap-[18px] xl:flex-row">
    <!-- Controles rápidos -->
    <section class="flex flex-col rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6 xl:w-[446px]">
      <div class="flex items-center justify-between">
        {@render sectionLabel("Controles rápidos")}
        <span class="font-mono text-[10px] tracking-[0.16em] text-[#8a96ad] uppercase">2 acciones</span>
      </div>
      <div class="mt-4 grid grid-cols-2 gap-2.5">
        <Button
          variant="outline"
          onclick={() => goto("/chat")}
          class="flex h-auto flex-col items-start gap-2 rounded-[10px] border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-4 text-left transition-colors hover:border-[rgba(159,197,255,0.25)] hover:bg-[rgba(180,210,255,0.04)]"
        >
          <span class="flex size-7 items-center justify-center rounded-[7px] bg-[rgba(159,197,255,0.14)] text-[#9fc5ff]">
            <Play class="size-3.5" />
          </span>
          <span class="font-heading text-base text-[#eaf1ff]">Iniciar guía</span>
          <span class="font-mono text-[10px] tracking-wide text-[#8a96ad]">Abrir el agente</span>
        </Button>
        <Button
          variant="outline"
          onclick={emergencyStop}
          disabled={stopping}
          class="flex h-auto flex-col items-start gap-2 rounded-[10px] border-[rgba(255,77,106,0.3)] bg-[rgba(255,77,106,0.05)] p-4 text-left transition-colors hover:border-[rgba(255,77,106,0.45)] hover:bg-[rgba(255,77,106,0.1)] disabled:opacity-70"
        >
          <span class="flex size-7 items-center justify-center rounded-[7px] bg-[rgba(255,77,106,0.12)] text-[#ff8aa0]">
            {#if stopping}
              <Loader2 class="size-3.5 animate-spin" />
            {:else}
              <Square class="size-3.5" />
            {/if}
          </span>
          <span class="font-heading text-base text-[#ff8aa0]">PARAR</span>
          <span class="font-mono text-[10px] tracking-wide text-[#8a96ad]">
            {stopMsg ?? "Detener movimiento"}
          </span>
        </Button>
      </div>
      <form
        onsubmit={runCommand}
        class="mt-auto flex items-center gap-2.5 rounded-[10px] border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-3"
      >
        <Send class="size-3.5 shrink-0 text-[#8a96ad]" />
        <Input
          bind:value={command}
          placeholder="Decile al robot qué hacer…"
          class="font-mono h-auto w-full rounded-none border-0 bg-transparent p-0 text-xs text-[#eaf1ff] shadow-none placeholder:text-[#8a96ad] focus-visible:ring-0"
        />
      </form>
    </section>

    <!-- Última ubicación conocida -->
    <section class="flex flex-1 flex-col rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6">
      <div class="flex items-center justify-between">
        {@render sectionLabel("Última ubicación conocida")}
        <Button
          variant="ghost"
          size="icon"
          aria-label="Expandir mapa"
          class="size-6 text-[#8a96ad] hover:bg-[rgba(180,210,255,0.06)] hover:text-[#eaf1ff]"
        >
          <Maximize2 class="size-3" />
        </Button>
      </div>
      <div class="relative mt-3.5 min-h-[260px] flex-1 overflow-hidden rounded-[10px] border border-[rgba(180,210,255,0.08)] bg-[radial-gradient(ellipse_at_center,#0c1220,#04070d)]">
        {#if live.trail.length > 1}
          <svg class="absolute inset-0 size-full" viewBox="0 0 100 100" preserveAspectRatio="none">
            <polyline
              points={trailPoints}
              fill="none"
              stroke="#9fc5ff"
              stroke-opacity="0.5"
              stroke-width="0.5"
              stroke-dasharray="0.6 1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        {/if}
        <!-- coords readout -->
        <span class="absolute top-2.5 left-3 font-mono text-[10px] tracking-wide text-[#8a96ad]">
          {#if pose}
            x {pose.x_meters_world.toFixed(2)} · y {pose.y_meters_world.toFixed(2)} m · {yawDeg}°
          {:else}
            sin posición
          {/if}
        </span>
        <!-- glowing marker at live pose -->
        <div class="absolute transition-[left,top] duration-500 ease-out" style="left:{marker.left}%;top:{marker.top}%">
          <span class="absolute -inset-4 rounded-full bg-[rgba(159,197,255,0.14)] blur-[5px]"></span>
          <span class="absolute -inset-2 rounded-full border {online ? 'border-[#9fc5ff]' : 'border-[#ff4d6a]'} opacity-50"></span>
          <span class="relative block size-3.5 rounded-full {online ? 'bg-[#9ae5f8]' : 'bg-[#ff4d6a]'} shadow-[0px_0px_24px_rgba(126,229,255,0.55),0px_0px_0px_3px_rgba(7,9,13,0.85)]"></span>
        </div>
      </div>
      <div class="mt-3.5 grid grid-cols-2 gap-2.5">
        <div class="flex items-center gap-2.5 rounded-lg border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-3">
          <MapPin class="size-3.5 shrink-0 text-[#8a96ad]" />
          <span class="flex-1 text-[13px] text-[#eaf1ff]">Distancia recorrida</span>
          <span class="font-mono text-sm text-[#9fc5ff]">{distanceKm.toFixed(2)} km</span>
        </div>
        <a href="/live-map" class="flex items-center gap-2.5 rounded-lg border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-3 transition-colors hover:border-[rgba(159,197,255,0.25)]">
          <Map class="size-3.5 shrink-0 text-[#8a96ad]" />
          <span class="flex-1 text-[13px] text-[#eaf1ff]">Ver mapa completo</span>
          <ChevronRight class="size-2.5 text-[#8a96ad]" />
        </a>
      </div>
    </section>
  </div>
</div>
