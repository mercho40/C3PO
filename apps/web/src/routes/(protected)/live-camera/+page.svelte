<script lang="ts">
  import { Camera, Share2, Maximize2 } from "@lucide/svelte";

  const detections = [
    { label: "person · 0.94", color: "#9ae5f8", left: "16%", top: "38%", width: "13%", height: "31%" },
    { label: "dog · 0.71", color: "#9fc5ff", left: "50%", top: "44%", width: "9%", height: "20%" },
    { label: "bicycle · 0.62", color: "#f5b643", left: "66%", top: "33%", width: "16%", height: "16%" },
  ];

  const thumbs = [
    { label: "Atrás", live: true },
    { label: "Izq.", live: true },
    { label: "Der.", live: true },
    { label: "IR · Noche", live: true },
  ];
</script>

{#snippet detBox(d: (typeof detections)[number])}
  <div
    class="absolute rounded-[2px] border"
    style="left:{d.left};top:{d.top};width:{d.width};height:{d.height};border-color:{d.color}"
  >
    {#each [["-top-px -left-px border-t-2 border-l-2", ""], ["-top-px -right-px border-t-2 border-r-2", ""], ["-bottom-px -left-px border-b-2 border-l-2", ""], ["-bottom-px -right-px border-b-2 border-r-2", ""]] as [pos] (pos)}
      <span class="absolute size-2 {pos}" style="border-color:{d.color}"></span>
    {/each}
    <span
      class="font-mono absolute -top-5 left-0 rounded-[3px] border px-1.5 py-0.5 text-[9px] tracking-wide whitespace-nowrap"
      style="border-color:{d.color};color:{d.color};background-color:{d.color}1f"
    >{d.label}</span>
  </div>
{/snippet}

<div class="flex h-full gap-[18px] pb-2">
  <!-- Main feed -->
  <section class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828]">
    <!-- Toolbar -->
    <div class="flex items-center gap-2 border-b border-[rgba(180,210,255,0.08)] bg-[#06121c] px-[18px] py-3.5">
      <button class="flex items-center gap-2 rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] px-3.5 py-1.5 font-mono text-[10px] tracking-[0.12em] text-[#eaf1ff] uppercase transition-colors hover:border-[rgba(159,197,255,0.25)]">
        <Camera class="size-3" /> Captura
      </button>
      <button class="flex items-center gap-2 rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] px-3.5 py-1.5 font-mono text-[10px] tracking-[0.12em] text-[#eaf1ff] uppercase transition-colors hover:border-[rgba(159,197,255,0.25)]">
        <Share2 class="size-3" /> Compartir
      </button>
      <button aria-label="Pantalla completa" class="flex size-[26px] items-center justify-center rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] text-[#eaf1ff] transition-colors hover:border-[rgba(159,197,255,0.25)]">
        <Maximize2 class="size-3" />
      </button>
    </div>

    <!-- Feed canvas -->
    <div class="relative min-h-0 flex-1 overflow-hidden bg-[#02050a]">
      <!-- perspective grid -->
      <svg class="absolute inset-0 size-full" preserveAspectRatio="none" viewBox="0 0 758 712">
        <defs>
          <linearGradient id="cam-floor" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#1a2030" />
            <stop offset="45%" stop-color="#06121c" />
            <stop offset="100%" stop-color="#060912" />
          </linearGradient>
        </defs>
        <rect width="758" height="712" fill="url(#cam-floor)" />
        <g stroke="rgba(180,210,255,0.10)" stroke-width="1">
          <!-- converging lines to vanishing point at 379,330 -->
          {#each [0, 84, 168, 252, 336, 422, 506, 590, 674, 758] as x (x)}
            <line x1={x} y1="712" x2="379" y2="330" />
          {/each}
          <!-- horizontal depth lines -->
          {#each [340, 360, 390, 440, 520, 640] as y (y)}
            <line x1="0" y1={y} x2="758" y2={y} />
          {/each}
        </g>
      </svg>

      <!-- detection boxes -->
      {#each detections as d (d.label)}
        {@render detBox(d)}
      {/each}

      <!-- crosshair -->
      <div class="absolute top-1/2 left-1/2 size-10 -translate-x-1/2 -translate-y-1/2">
        <span class="absolute top-0 left-1/2 h-3 w-px bg-[#9ae5f8]"></span>
        <span class="absolute bottom-0 left-1/2 h-3 w-px bg-[#9ae5f8]"></span>
        <span class="absolute top-1/2 left-0 h-px w-3 bg-[#9ae5f8]"></span>
        <span class="absolute top-1/2 right-0 h-px w-3 bg-[#9ae5f8]"></span>
      </div>

      <!-- top-left overlay -->
      <div class="font-mono absolute top-4 left-4 flex flex-col gap-1 text-[11px] tracking-wide">
        <span class="text-[#9ae5f8]">● EN VIVO</span>
        <span class="text-[#eaf1ff]">FOV 110° · IR off</span>
        <span class="text-[#8a96ad]">+47.6062, -122.3321</span>
      </div>
      <!-- top-right timestamp -->
      <div class="font-mono absolute top-4 right-4 flex flex-col items-end gap-1 text-[11px] text-[#8a96ad]">
        <span>14:10:56</span>
        <span>2026-05-18</span>
      </div>
      <!-- footer -->
      <div class="font-mono absolute inset-x-4 bottom-4 flex items-center justify-between text-[10px] tracking-wide text-[#8a96ad]">
        <span>DETECCIÓN · 3 objetos</span>
        <span>LATENCIA 14 ms</span>
      </div>
    </div>
  </section>

  <!-- Other cameras -->
  <aside class="flex w-[400px] shrink-0 flex-col gap-4 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-4">
    <div class="flex items-center justify-between">
      <span class="text-[10px] font-medium tracking-[0.18em] text-[#8a96ad] uppercase">Otras cámaras</span>
      <span class="font-mono text-[10px] text-[#8a96ad]">3</span>
    </div>
    <div class="flex min-h-0 flex-1 flex-col gap-4">
      {#each thumbs as t (t.label)}
        <div class="relative min-h-0 flex-1 overflow-hidden rounded-lg border border-[rgba(180,210,255,0.08)] bg-[#0a0e14]">
          <div class="absolute inset-0 bg-[radial-gradient(ellipse_at_center,#0c1220,#060912)]"></div>
          <span class="font-mono absolute top-2 left-2.5 flex items-center gap-1.5 text-[9px] tracking-wide text-[#9ae5f8] uppercase">
            <span class="size-1.5 rounded-full bg-[#9ae5f8] shadow-[0px_0px_8px_#7ee5ff]"></span>
            {t.label}
          </span>
        </div>
      {/each}
    </div>
  </aside>
</div>
