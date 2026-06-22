<script lang="ts">
  import CircularProgress from "$lib/components/circular-progress.svelte";
  import { Play, Square, Send, MapPin, Maximize2, ChevronRight, Map } from "@lucide/svelte";

  let { data } = $props();

  const robot = $derived(data.state);
  const online = $derived(data.online);
  const battery = $derived(Math.round(robot?.battery_pct ?? 0));
  const faults = $derived(robot?.faults ?? []);

  // Telemetry metrics (cadence/torque/IMU/CPU) are not in the bridge state model,
  // so these are representative values until the backend exposes them.
  const telemetry = [
    { label: "Cadencia del paso", value: "1.24", unit: "Hz", pct: 62 },
    { label: "Torque articular (prom.)", value: "48.2", unit: "Nm", pct: 55 },
    { label: "Inclinación IMU", value: "-2.4", unit: "°", pct: 18 },
    { label: "Proximidad obstáculo", value: "2.0", unit: "m", pct: 8 },
    { label: "Carga CPU", value: "38", unit: "%", pct: 38 },
  ];

  let command = $state("");
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
        <span class="flex items-center gap-2 rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.04)] px-3 py-1.5">
          <span class="size-1.5 rounded-sm {online ? 'bg-[#5ee7a1] shadow-[0px_0px_10px_rgba(94,231,161,0.6)]' : 'bg-[#ff4d6a] shadow-[0px_0px_10px_rgba(255,77,106,0.6)]'}"></span>
          <span class="font-mono text-[11px] tracking-[0.1em] text-[#8a96ad] uppercase">{online ? "Conectado" : "Offline"}</span>
        </span>
      </div>
      <div class="mt-5 grid grid-cols-2 gap-2.5">
        {@render statTile("Red", online ? `${data.latencyMs} ms` : "—")}
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
            <div class="h-1 w-full overflow-hidden rounded-full bg-[rgba(180,210,255,0.08)]">
              <div
                class="h-full rounded-full bg-gradient-to-r from-[#4a7dd1] to-[#7ee5ff] shadow-[0px_0px_12px_rgba(126,229,255,0.55)]"
                style="width:{m.pct}%"
              ></div>
            </div>
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
          <span class="font-mono text-[9px] tracking-[0.16em] text-[#8a96ad] uppercase">Alcance</span>
          <span class="text-[15px] text-[#eaf1ff]">6.8 km</span>
        </div>
        <div class="flex flex-col gap-1">
          <span class="font-mono text-[9px] tracking-[0.16em] text-[#8a96ad] uppercase">Tiempo</span>
          <span class="text-[15px] text-[#eaf1ff]">2 h 14 m</span>
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
        <button class="flex flex-col items-start gap-2 rounded-[10px] border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-4 text-left transition-colors hover:border-[rgba(159,197,255,0.25)]">
          <span class="flex size-7 items-center justify-center rounded-[7px] bg-[rgba(159,197,255,0.14)] text-[#9fc5ff]">
            <Play class="size-3.5" />
          </span>
          <span class="font-heading text-base text-[#eaf1ff]">Iniciar guía</span>
          <span class="font-mono text-[10px] tracking-wide text-[#8a96ad]">Activar modo ruta</span>
        </button>
        <button class="flex flex-col items-start gap-2 rounded-[10px] border border-[rgba(255,77,106,0.3)] bg-[rgba(255,77,106,0.05)] p-4 text-left transition-colors hover:bg-[rgba(255,77,106,0.1)]">
          <span class="flex size-7 items-center justify-center rounded-[7px] bg-[rgba(255,77,106,0.12)] text-[#ff8aa0]">
            <Square class="size-3.5" />
          </span>
          <span class="font-heading text-base text-[#ff8aa0]">PARAR</span>
          <span class="font-mono text-[10px] tracking-wide text-[#8a96ad]">Detener movimiento</span>
        </button>
      </div>
      <form class="mt-auto flex items-center gap-2.5 rounded-[10px] border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-3" onsubmit={(e) => e.preventDefault()}>
        <Send class="size-3.5 shrink-0 text-[#8a96ad]" />
        <input
          bind:value={command}
          placeholder="Decile al robot qué hacer…"
          class="font-mono w-full bg-transparent text-xs text-[#eaf1ff] placeholder:text-[#8a96ad] focus:outline-none"
        />
      </form>
    </section>

    <!-- Última ubicación conocida -->
    <section class="flex flex-1 flex-col rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6">
      <div class="flex items-center justify-between">
        {@render sectionLabel("Última ubicación conocida")}
        <Maximize2 class="size-3 text-[#8a96ad]" />
      </div>
      <div class="relative mt-3.5 min-h-[260px] flex-1 overflow-hidden rounded-[10px] border border-[rgba(180,210,255,0.08)] bg-[radial-gradient(ellipse_at_center,#0c1220,#04070d)]">
        <svg class="absolute inset-0 size-full" viewBox="0 0 707 309" preserveAspectRatio="none">
          <path
            d="M0 250 C 120 230, 200 170, 300 160 S 520 110, 707 70"
            fill="none"
            stroke="#9fc5ff"
            stroke-opacity="0.5"
            stroke-width="2"
            stroke-dasharray="2 7"
            stroke-linecap="round"
          />
        </svg>
        <!-- glowing marker -->
        <div class="absolute" style="left:42%;top:50%">
          <span class="absolute -inset-4 rounded-full bg-[rgba(159,197,255,0.14)] blur-[5px]"></span>
          <span class="absolute -inset-2 rounded-full border border-[#9fc5ff] opacity-50"></span>
          <span class="relative block size-3.5 rounded-full bg-[#9ae5f8] shadow-[0px_0px_24px_rgba(126,229,255,0.55),0px_0px_0px_3px_rgba(7,9,13,0.85)]"></span>
        </div>
      </div>
      <div class="mt-3.5 grid grid-cols-2 gap-2.5">
        <div class="flex items-center gap-2.5 rounded-lg border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-3">
          <MapPin class="size-3.5 shrink-0 text-[#8a96ad]" />
          <span class="flex-1 text-[13px] text-[#eaf1ff]">Distancia recorrida</span>
          <span class="font-mono text-sm text-[#9fc5ff]">0.3 km</span>
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
