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
   *
   * THE PRESS IS NOT THIS COMPONENT'S. It lives in `#lib/robot/estop.svelte`, so
   * the topbar's button and the one /vr-control mounts inside the headset's
   * dom-overlay show the same press. What the press means — and when "Parando…"
   * may become "Detenido" — is decided in `#lib/robot/estop.ts`: only a
   * telemetry reading taken after the order confirms it, offline is never
   * confirmation, and only a confirmed stop clears itself.
   *
   * Announcements (the polite "Parando…/Detenido" and the failure alerts) are
   * rendered ONCE, by the shell's EstopBanner — two buttons must not announce
   * the same press twice.
   */
  import { Square, Check, TriangleAlert, Loader2 } from "@lucide/svelte";
  import { getRobotLive } from "#lib/robot/context.js";
  import { readPosture } from "#lib/robot/posture.js";
  import { estop } from "#lib/robot/estop.svelte.js";
  import { estopView, STOP_HOTKEY_LABEL } from "#lib/robot/estop.js";

  const live = getRobotLive();
  // "Under power and free to move": a gait controller is loaded (it takes a
  // step the moment it is sent a velocity, even standing still), or the
  // posture is motion by definition (a dance). Either way, the stop fills in.
  const posture = $derived(readPosture(live.state?.posture, live.online));
  const moving = $derived(posture.locomotion || posture.load === "moving");
  const view = $derived(estopView(estop.phase));

  function press() {
    // aria-disabled rather than `disabled`: a disabled button drops keyboard
    // focus mid-press, and the operator who just hit it should still be on it.
    if (estop.busy) return;
    void estop.press();
  }
</script>

<button
  type="button"
  onclick={press}
  aria-disabled={estop.busy}
  aria-keyshortcuts="Shift+Space"
  title="Parar todo movimiento del robot ({STOP_HOTKEY_LABEL})"
  data-moving={moving}
  data-tone={view.tone}
  class="stop"
>
  <span class="icon" aria-hidden="true">
    {#if view.spinning}
      <Loader2 class="size-4 motion-safe:animate-spin" />
    {:else if view.tone === "stopped"}
      <Check class="size-4" />
    {:else if view.tone === "failed" || view.tone === "unconfirmed"}
      <TriangleAlert class="size-4" />
    {:else}
      <Square class="size-4" />
    {/if}
  </span>
  <!-- The visible word IS the accessible name (plus a suffix saying what the
       button does), so "Reintentar" said aloud to voice control matches what is
       on screen. It used to carry a fixed aria-label that hid every state. -->
  <span class="label hidden sm:inline">{view.label}</span>
  <span class="label sm:hidden">{view.short}</span>
  <span class="sr-only"> — detener todo movimiento del robot</span>
</button>

<style>
  .stop {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.375rem;
    /* 44px minimum on both axes: this is the control that halts a 1.3m humanoid,
       and the device it is pressed on is a phone held by someone standing next
       to that humanoid. `min-height` rather than `height` so it can grow with
       the longer confirmation labels. */
    min-height: 2.75rem;
    min-width: 2.75rem;
    padding-inline: 0.75rem;
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

  @media (min-width: 40rem) {
    .stop {
      gap: 0.5rem;
      padding-inline: 0.875rem;
    }
  }

  .stop:focus-visible {
    outline: 2px solid var(--c3-cyan-hot);
    outline-offset: 2px;
  }

  .stop:hover:not([aria-disabled="true"]) {
    background-color: color-mix(in srgb, var(--c3-danger) 18%, transparent);
    border-color: color-mix(in srgb, var(--c3-danger) 70%, transparent);
    color: var(--c3-on-danger);
  }

  .stop[aria-disabled="true"] {
    opacity: 0.75;
    cursor: progress;
  }

  .icon {
    display: inline-flex;
  }

  /* Under power the control stops being a precaution and becomes the thing you
     are most likely to reach for, so it fills in. */
  .stop[data-moving="true"] {
    background-color: var(--c3-danger);
    border-color: var(--c3-danger);
    color: var(--c3-on-danger);
  }

  /* The order was sent but nothing can confirm it. Amber, not green: this is an
     open question, not a confirmation. */
  .stop[data-tone="unconfirmed"] {
    border-color: color-mix(in srgb, var(--c3-warn) 55%, transparent);
    background-color: color-mix(in srgb, var(--c3-warn) 12%, transparent);
    color: var(--c3-warn);
  }

  .stop[data-tone="stopped"] {
    border-color: color-mix(in srgb, var(--c3-ok) 45%, transparent);
    background-color: color-mix(in srgb, var(--c3-ok) 12%, transparent);
    color: var(--c3-ok);
  }

  .stop[data-tone="failed"] {
    background-color: var(--c3-danger);
    border-color: var(--c3-on-danger);
    color: var(--c3-on-danger);
  }
</style>
