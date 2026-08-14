<script lang="ts">
  /**
   * Emergency stop, in the console shell.
   *
   * This used to be one tile of two on the dashboard, which meant that the
   * moment an operator was most likely to need it — watching the camera feed, or
   * reading the map while the robot walked — it was one navigation away. For a
   * machine that can put a person on the floor, the stop belongs in the frame,
   * not on a page.
   *
   * It is quiet while the robot is stationary and asserts itself the moment the
   * robot is under power, which is the only time it is urgent.
   */
  import { Square, Check, TriangleAlert, Loader2 } from "@lucide/svelte";
  import { goto } from "$app/navigation";
  import { createApi } from "$lib/api";
  import { getRobotLive } from "$lib/robot/context";
  import { readPosture } from "$lib/robot/posture";

  const live = getRobotLive();
  const moving = $derived(
    readPosture(live.state?.posture, live.online).load === "moving",
  );

  /** What the last press did, as far as we can actually tell. */
  type Outcome = "requested" | "rejected" | null;
  let busy = $state(false);
  let outcome = $state<Outcome>(null);
  let timer: ReturnType<typeof setTimeout> | null = null;

  /**
   * The button reports what it observes, not what it hopes.
   *
   * A 200 from `/skills/stop_everything/invoke` means the request was accepted,
   * which is not the same as the robot having stopped — so the label stays
   * "Parando…" until the live `/state` poll actually shows the robot out of a
   * locomotion mode. Only then does it say "Detenido". Claiming a humanoid has
   * halted on the strength of an HTTP status is the kind of thing an operator
   * would only discover was wrong by watching it keep walking.
   */
  const label = $derived.by(() => {
    if (outcome === "rejected") return "Reintentar";
    if (busy || (outcome === "requested" && moving)) return "Parando…";
    if (outcome === "requested") return "Detenido";
    return "Parar";
  });

  const tone = $derived.by(() => {
    if (outcome === "rejected") return "failed";
    if (outcome === "requested" && !moving && !busy) return "stopped";
    return "idle";
  });

  async function stop() {
    if (busy) return;
    busy = true;
    outcome = null;
    if (timer) clearTimeout(timer);
    try {
      const { error } = await createApi(fetch)
        .skills({ name: "stop_everything" })
        .invoke.post({});
      if (error && (error.status as number) === 401) {
        // A generic "rejected" here reads as "the robot refused the stop" --
        // the actual problem is the session expired, and retrying the same
        // request will just fail the same way. Send the operator to re-auth
        // instead of leaving them to press a button that can't work.
        await goto("/login");
        return;
      }
      outcome = error ? "rejected" : "requested";
    } catch {
      outcome = "rejected";
    } finally {
      busy = false;
      // A rejection stays put — it needs a decision, not a timeout. A confirmed
      // stop clears itself.
      if (outcome === "requested")
        timer = setTimeout(() => (outcome = null), 6000);
    }
  }
</script>

<button
  type="button"
  onclick={stop}
  disabled={busy}
  data-moving={moving}
  data-tone={tone}
  class="stop"
  aria-label="Parar todo movimiento del robot"
>
  <span class="icon" aria-hidden="true">
    {#if busy || (outcome === "requested" && moving)}
      <Loader2 class="size-4 animate-spin" />
    {:else if tone === "stopped"}
      <Check class="size-4" />
    {:else if tone === "failed"}
      <TriangleAlert class="size-4" />
    {:else}
      <Square class="size-4" />
    {/if}
  </span>
  <span class="label">{label}</span>
</button>

{#if outcome === "rejected"}
  <!-- The one failure on this console that an operator must not scroll past:
       the robot did not get the order and may still be moving. -->
  <p role="alert" class="sr-only">
    No se pudo enviar la orden de parada. El robot puede seguir en movimiento.
  </p>
{/if}

<style>
  .stop {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    /* 44px minimum on both axes: this is the control that halts a 1.3m humanoid,
       and the device it is pressed on is a phone held by someone standing next
       to that humanoid. `min-height` rather than `height` so it can grow with
       the longer confirmation labels. */
    min-height: 2.75rem;
    min-width: 2.75rem;
    padding-inline: 0.875rem;
    border-radius: var(--radius-lg);
    border: 1px solid color-mix(in srgb, var(--c3-danger) 40%, transparent);
    background-color: color-mix(in srgb, var(--c3-danger) 8%, transparent);
    color: var(--c3-danger-soft);
    font-family: var(--font-display);
    font-variation-settings:
      "wdth" 116,
      "wght" 700;
    font-size: 0.8125rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    white-space: nowrap;
    transition:
      background-color 150ms,
      border-color 150ms,
      color 150ms;
  }

  .stop:hover:not(:disabled) {
    background-color: color-mix(in srgb, var(--c3-danger) 18%, transparent);
    border-color: color-mix(in srgb, var(--c3-danger) 70%, transparent);
    color: #fff;
  }

  .stop:disabled {
    opacity: 0.75;
  }

  .icon {
    display: inline-flex;
  }

  /* Under power the control stops being a precaution and becomes the thing you
     are most likely to reach for, so it fills in. */
  .stop[data-moving="true"] {
    background-color: var(--c3-danger);
    border-color: var(--c3-danger);
    color: #fff;
  }

  .stop[data-tone="stopped"] {
    border-color: color-mix(in srgb, var(--c3-ok) 45%, transparent);
    background-color: color-mix(in srgb, var(--c3-ok) 12%, transparent);
    color: var(--c3-ok);
  }

  .stop[data-tone="failed"] {
    background-color: var(--c3-danger);
    border-color: #fff;
    color: #fff;
  }
</style>
