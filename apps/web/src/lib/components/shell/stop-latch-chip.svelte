<script lang="ts">
  /*
   * "PARAR ACTIVO · Reanudar" — the robot is stopped and stays stopped.
   *
   * After PARAR the bridge refuses every motion tool, from the agent and from
   * this console, until an operator resumes. Without this chip the only sign
   * was every button failing with "PARAR is standing". Shown only when the
   * bridge SAYS a stop stands; a failed read keeps the last answer.
   */
  import { OctagonX, Loader2 } from "@lucide/svelte";
  import { getStopLatch } from "#lib/robot/context.js";
  import { stoppedAgo } from "#lib/robot/stop-latch.js";

  const latch = getStopLatch();
  const ago = $derived(stoppedAgo(latch.status, Date.now()));
</script>

<span role="status" class="sr-only"
  >{latch.standing
    ? "PARAR activo: el robot no se mueve hasta reanudar"
    : ""}</span
>

{#if latch.standing}
  <span
    class="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-danger/45 bg-danger/10 pr-1 pl-2 text-xs text-danger-soft"
    title={`PARAR activo${ago ? ` (${ago})` : ""}: el robot rechaza todo movimiento, también del agente, hasta que un operador reanude.`}
  >
    <OctagonX class="size-3.5 shrink-0" aria-hidden="true" />
    <span
      class="hidden readout font-medium tracking-[0.12em] whitespace-nowrap uppercase sm:inline"
      >Parar activo</span
    >
    <button
      type="button"
      onclick={() => void latch.resume()}
      aria-disabled={latch.resuming}
      class="inline-flex h-6 items-center gap-1 rounded-full border border-danger/45 px-2 font-medium text-ink hover:bg-danger/20 focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
    >
      {#if latch.resuming}<Loader2
          class="size-3 motion-safe:animate-spin"
          aria-hidden="true"
        />{/if}
      Reanudar
    </button>
  </span>
{/if}
{#if latch.error}
  <span role="alert" class="text-xs text-danger-soft">{latch.error}</span>
{/if}
