<script lang="ts">
  import { Navigation, Clock, Plus, Minus } from "@lucide/svelte";

  const layers = ["Satélite", "Oscuro", "Terreno"];
  let activeLayer = $state("Oscuro");

  const units = [
    {
      id: "BIPED-01",
      tag: "Guía",
      kind: "Cuadrúpedo · 12 DOF",
      seen: "activo hace 2 h",
      battery: 84,
      active: true,
    },
    {
      id: "BIPED-02",
      tag: "En espera",
      kind: "Humanoide · 28 DOF",
      seen: "activo hace 5 h",
      battery: 67,
      active: false,
    },
  ];
</script>

{#snippet unitCard(u: (typeof units)[number])}
  <div
    class="flex flex-col gap-2.5 rounded-lg border p-3.5 {u.active
      ? 'border-[rgba(159,197,255,0.3)] bg-[rgba(159,197,255,0.14)]'
      : 'border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)]'}"
  >
    <div class="flex items-center gap-2.5">
      <span class="flex items-center rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.04)] px-1.5 py-1">
        <span class="size-1.5 rounded-sm bg-[#5ee7a1] shadow-[0px_0px_10px_rgba(94,231,161,0.6)]"></span>
      </span>
      <span class="font-mono text-xs tracking-wide text-[#eaf1ff]">{u.id}</span>
      <span class="ml-auto font-mono text-[9px] tracking-[0.09em] text-[#8a96ad] uppercase">{u.tag}</span>
    </div>
    <span class="font-mono text-[10px] tracking-wide text-[#8a96ad]">{u.kind}</span>
    <div class="flex items-center gap-2 text-[#8a96ad]">
      <Clock class="size-3.5 shrink-0" />
      <span class="text-[11px]">{u.seen}</span>
    </div>
    <div class="flex items-center gap-2">
      <div class="h-1 flex-1 overflow-hidden rounded-full bg-[rgba(180,210,255,0.08)]">
        <div
          class="h-full rounded-full bg-gradient-to-r from-[#4a7dd1] to-[#7ee5ff] shadow-[0px_0px_12px_rgba(126,229,255,0.55)]"
          style="width:{u.battery}%"
        ></div>
      </div>
      <span class="font-mono text-[11px] text-[#eaf1ff]">{u.battery}%</span>
    </div>
  </div>
{/snippet}

<div class="flex h-full gap-[18px] pb-2">
  <!-- Map -->
  <section class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828]">
    <!-- Layer toolbar -->
    <div class="flex items-center gap-2 border-b border-[rgba(180,210,255,0.08)] bg-[#06121c] px-[18px] py-3.5">
      {#each layers as layer (layer)}
        <button
          onclick={() => (activeLayer = layer)}
          class="rounded-full border px-3.5 py-1.5 font-mono text-[10px] tracking-[0.12em] uppercase transition-colors {activeLayer ===
          layer
            ? 'border-transparent bg-gradient-to-b from-[#c6dcff] to-[#9fc5ff] font-bold text-[#06121c] shadow-[0px_8px_28px_-10px_rgba(126,229,255,0.55)]'
            : 'border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] text-[#eaf1ff]'}"
        >
          {layer}
        </button>
      {/each}
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
        <path
          d="M40 560 C 200 520, 320 430, 430 415 S 660 350, 760 300"
          fill="none"
          stroke="#9fc5ff"
          stroke-opacity="0.5"
          stroke-width="2"
          stroke-dasharray="2 7"
          stroke-linecap="round"
        />
      </svg>

      <!-- BIPED-02 marker (small) -->
      <div class="absolute" style="left:69%;top:30%">
        <span class="absolute -inset-2.5 rounded-full border border-[#9ae5f8] opacity-50"></span>
        <span class="relative block size-3 rounded-full bg-[#9ae5f8] shadow-[0px_0px_24px_#7ee5ff,0px_0px_0px_3px_rgba(5,7,13,0.85)]"></span>
        <span class="absolute -top-6 left-1/2 -translate-x-1/2 rounded-md border border-[rgba(180,210,255,0.18)] bg-[rgba(5,7,13,0.85)] px-2 py-1 font-mono text-[10px] tracking-wide whitespace-nowrap text-[#eaf1ff] backdrop-blur-sm">BIPED-02</span>
      </div>

      <!-- BIPED-01 marker (primary) -->
      <div class="absolute" style="left:43%;top:46%">
        <span class="absolute -inset-7 rounded-full bg-[rgba(159,197,255,0.14)] blur-[7px]"></span>
        <span class="absolute -inset-3.5 rounded-full border border-[#9ae5f8] opacity-50"></span>
        <span class="relative block size-4 rounded-full bg-[#9ae5f8] shadow-[0px_0px_24px_#7ee5ff,0px_0px_0px_3px_rgba(5,7,13,0.85)]"></span>
        <span class="absolute -top-7 left-1/2 -translate-x-1/2 rounded-md border border-[rgba(180,210,255,0.18)] bg-[rgba(5,7,13,0.85)] px-2 py-1 font-mono text-[10px] tracking-wide whitespace-nowrap text-[#eaf1ff] backdrop-blur-sm">BIPED-01</span>
      </div>

      <!-- Compass -->
      <div class="absolute top-5 right-5 flex size-[60px] flex-col items-center justify-center rounded-full border border-[rgba(180,210,255,0.18)] bg-[rgba(5,7,13,0.6)] backdrop-blur-sm">
        <Navigation class="size-3.5 text-[#7ee5ff]" />
        <span class="font-mono text-[10px] tracking-wide text-[#9fc5ff]">N</span>
      </div>

      <!-- Scale -->
      <div class="absolute bottom-5 left-5 flex flex-col gap-1">
        <span class="h-px w-20 bg-[#8a96ad]"></span>
        <span class="font-mono text-[10px] text-[#8a96ad]">200 m</span>
      </div>

      <!-- Zoom -->
      <div class="absolute right-5 bottom-5 flex flex-col overflow-hidden rounded-lg border border-[rgba(180,210,255,0.18)] bg-[rgba(5,7,13,0.6)] backdrop-blur-sm">
        <button aria-label="Acercar" class="flex size-8 items-center justify-center text-[#eaf1ff] transition-colors hover:bg-[rgba(180,210,255,0.06)]">
          <Plus class="size-3" />
        </button>
        <span class="h-px w-full bg-[rgba(180,210,255,0.08)]"></span>
        <button aria-label="Alejar" class="flex size-8 items-center justify-center text-[#eaf1ff] transition-colors hover:bg-[rgba(180,210,255,0.06)]">
          <Minus class="size-3" />
        </button>
      </div>
    </div>
  </section>

  <!-- Units panel -->
  <aside class="flex w-[340px] shrink-0 flex-col gap-2.5 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-3.5">
    {#each units as u (u.id)}
      {@render unitCard(u)}
    {/each}
    <button class="flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-[rgba(180,210,255,0.18)] p-3 font-mono text-[11px] tracking-[0.1em] text-[#8a96ad] uppercase transition-colors hover:border-[rgba(159,197,255,0.4)] hover:text-[#c6dcff]">
      <Plus class="size-3" />
      Agregar unidad
    </button>
  </aside>
</div>
