<script lang="ts">
  import { Mic, ArrowRight } from "@lucide/svelte";

  type Msg =
    | { kind: "user" | "bot"; text: string; time: string }
    | { kind: "system"; text: string };

  const messages: Msg[] = [
    { kind: "user", text: "Camina hasta la salida", time: "14:24 · VOS" },
    { kind: "bot", text: "Trazando ruta a la salida · 10 m, ETA 02:14. Ritmo lento", time: "14:24 · C3PO" },
    { kind: "system", text: "◆ Plan aceptado · modo autónomo activo" },
    { kind: "user", text: "Pará", time: "14:27 · VOS" },
    { kind: "bot", text: 'Entendido. Me detengo en WP-02 y espero tu "seguí" para continuar.', time: "14:27 · C3PO" },
    { kind: "system", text: "◆ Peatón detectado · bajando a 0.4 m/s" },
    { kind: "user", text: "¿Batería?", time: "14:30 · VOS" },
    { kind: "bot", text: "Estoy en 84.6%. Alcance estimado 6.8 km. Estás dentro del rango operativo.", time: "14:30 · C3PO" },
  ];

  const task = { title: "Caminar hasta la salida", distance: "247 m", eta: "02:14", pace: "lento" };
  const suggestions = ["Llevame a casa", "Sentate y esperá", "Más lento", "Pasar a modo manual"];

  let draft = $state("");
</script>

<div class="flex h-full gap-3.5 pb-2">
  <!-- Conversation -->
  <section class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828]">
    <!-- Header -->
    <div class="flex items-center gap-2.5 border-b border-[rgba(180,210,255,0.08)] bg-[#06121c] px-[18px] py-3.5">
      <span class="flex size-7 items-center justify-center rounded-lg bg-gradient-to-br from-[#9fc5ff] to-[#4a7dd1]">
        <img src="/logo.svg" alt="" class="size-4 object-contain" />
      </span>
      <span class="font-mono text-xs tracking-wide text-[#eaf1ff]">C3PO · BIPED-01</span>
      <span class="flex items-center gap-2 rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.04)] px-3 py-1.5">
        <span class="size-1.5 rounded-sm bg-[#5ee7a1] shadow-[0px_0px_10px_rgba(94,231,161,0.6)]"></span>
        <span class="font-mono text-[11px] tracking-[0.1em] text-[#8a96ad] uppercase">Escuchando</span>
      </span>
    </div>

    <!-- Messages -->
    <div class="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto p-5">
      {#each messages as m (m.text)}
        {#if m.kind === "system"}
          <div class="flex justify-center">
            <span class="rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.04)] px-3.5 py-1.5 font-mono text-[10px] tracking-wide text-[#8a96ad]">{m.text}</span>
          </div>
        {:else if m.kind === "user"}
          <div class="flex flex-col items-end gap-1">
            <div class="max-w-[70%] rounded-[27px] bg-[#4280df] px-[17px] py-3 text-sm leading-relaxed text-[#06121c]">{m.text}</div>
            <span class="font-mono text-[9px] tracking-wide text-[#8a96ad]">{m.time}</span>
          </div>
        {:else}
          <div class="flex flex-col items-start gap-1">
            <div class="max-w-[70%] rounded-[27px] border border-[rgba(180,210,255,0.18)] bg-[#0c1220] px-[17px] py-3 text-sm leading-relaxed text-[#eaf1ff]">{m.text}</div>
            <span class="font-mono text-[9px] tracking-wide text-[#8a96ad]">{m.time}</span>
          </div>
        {/if}
      {/each}
    </div>

    <!-- Input -->
    <div class="border-t border-[rgba(180,210,255,0.08)] bg-[#06121c] p-[18px]">
      <form class="flex items-center gap-3 rounded-xl border border-[rgba(180,210,255,0.18)] bg-[#0c1220] p-3" onsubmit={(e) => e.preventDefault()}>
        <button type="button" aria-label="Hablar" class="shrink-0 text-[#8a96ad] transition-colors hover:text-[#eaf1ff]">
          <Mic class="size-3.5" />
        </button>
        <input
          bind:value={draft}
          placeholder="Decile a C3PO qué hacer…"
          class="font-mono h-5 w-full bg-transparent text-[13px] text-[#eaf1ff] placeholder:text-[#8a96ad] focus:outline-none"
        />
        <button
          type="submit"
          class="flex shrink-0 items-center gap-2 rounded-full bg-gradient-to-b from-[#c6dcff] to-[#9fc5ff] px-4 py-2 font-mono text-[12px] font-bold tracking-[0.12em] text-[#06121c] uppercase shadow-[0px_8px_28px_-10px_rgba(126,229,255,0.55)]"
        >
          Enviar
          <ArrowRight class="size-3.5" />
        </button>
      </form>
    </div>
  </section>

  <!-- Side panel -->
  <aside class="flex w-[346px] shrink-0 flex-col gap-3.5">
    <div class="flex flex-col gap-3 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6">
      <span class="text-[10px] font-medium tracking-[0.18em] text-[#8a96ad] uppercase">Tarea actual</span>
      <h2 class="font-heading text-lg font-bold tracking-tight text-[#eaf1ff]">{task.title}</h2>
      <div class="flex flex-col gap-2 pt-1">
        <div class="flex items-center justify-between">
          <span class="font-mono text-[10px] tracking-wide text-[#8a96ad] uppercase">Distancia</span>
          <span class="font-mono text-[11px] text-[#eaf1ff]">{task.distance}</span>
        </div>
        <div class="flex items-center justify-between">
          <span class="font-mono text-[10px] tracking-wide text-[#8a96ad] uppercase">ETA</span>
          <span class="font-mono text-[11px] text-[#eaf1ff]">{task.eta}</span>
        </div>
        <div class="flex items-center justify-between">
          <span class="font-mono text-[10px] tracking-wide text-[#8a96ad] uppercase">Ritmo</span>
          <span class="font-mono text-[11px] text-[#eaf1ff]">{task.pace}</span>
        </div>
      </div>
    </div>

    <div class="flex flex-col gap-4 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-6">
      <span class="text-[10px] font-medium tracking-[0.18em] text-[#8a96ad] uppercase">Sugerencias</span>
      <div class="flex flex-col gap-2">
        {#each suggestions as s (s)}
          <button
            onclick={() => (draft = s)}
            class="rounded-lg border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.02)] p-2.5 text-left text-xs text-[#8a96ad] transition-colors hover:border-[rgba(159,197,255,0.25)] hover:text-[#c6dcff]"
          >"{s}"</button>
        {/each}
      </div>
    </div>
  </aside>
</div>
