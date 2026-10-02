<script lang="ts">
  /*
   * "NAV ARMADO · 23 s" — something other than an operator can drive.
   *
   * `arm_navigation` (danger "high") opens the Nav2 cmd_vel gate for up to 120 s,
   * and the agent can call it from a chat. Nothing in the console used to show
   * that the robot was armed for autonomous driving, let alone for how long, so
   * this sits next to the e-stop on every page while it is.
   *
   * Only shown when the bridge SAYS the gate is open (`Gate.armed`) — never
   * inferred. If the gate poll starts failing after that, the chip stays up and
   * says it can no longer confirm: an armed gate we cannot read is not a closed
   * gate.
   *
   * The countdown runs locally between polls from the last `arm_expires_in_s`,
   * because the gate closes itself on the bridge whether or not we are polling.
   */
  import { Navigation } from "@lucide/svelte";
  import { createSubscriber } from "svelte/reactivity";
  import { resolve } from "$app/paths";
  import { getGate } from "$lib/robot/context";

  const gate = getGate();

  // A 1 s clock that only ticks while something reads it — i.e. while the chip
  // is on screen. No interval runs for the 99 % of the time nothing is armed.
  const tick = createSubscriber((update) => {
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  });
  const now = () => {
    tick();
    return Date.now();
  };

  const stale = $derived(gate.failures > 0);
  const remaining = $derived.by(() => {
    const s = gate.status?.arm_expires_in_s;
    if (s == null || gate.lastOkAt == null) return null;
    const elapsed = Math.max(0, now() - gate.lastOkAt) / 1000;
    return Math.max(0, Math.ceil(s - elapsed));
  });
  // Null means no planner is publishing at all: armed, but nothing to forward.
  const noPlanner = $derived(gate.status?.cmd_vel_age_s == null);

  const countdown = $derived(
    stale ? "sin datos" : remaining == null ? null : `${remaining} s`,
  );
  const sentence = $derived(
    [
      "Navegación autónoma armada: Nav2 puede mover el robot",
      stale
        ? "no se pudo volver a leer el estado de la compuerta"
        : remaining != null
          ? `se desarma sola en ${remaining} s`
          : null,
      !stale && noPlanner ? "sin planificador publicando" : null,
    ]
      .filter(Boolean)
      .join(" — "),
  );
</script>

<!-- Persistent region: arming from a chat must be announced, not just drawn.
     Only the arming itself — a per-second countdown would be noise. -->
<span role="status" class="sr-only"
  >{gate.armed ? "Navegación autónoma armada" : ""}</span
>

{#if gate.armed}
  <a
    href={resolve("/health")}
    title={sentence}
    class="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-danger/45 bg-danger/10 px-2 text-xs text-danger-soft transition-colors hover:bg-danger/20 focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none focus-visible:ring-inset sm:px-2.5"
  >
    <Navigation class="size-3.5 shrink-0" aria-hidden="true" />
    <!-- On a phone: the glyph and the countdown — the chip shares the topbar's
         second row with the status, and with the word it pushed that row onto
         a third. The red tint, the title and the accessible name say what it
         is. "Sin planificador" only where there is room to spare. -->
    <span
      class="readout font-medium tracking-[0.12em] whitespace-nowrap text-[inherit] uppercase"
    >
      <span class="hidden sm:inline">Nav armado</span>
      {#if countdown}
        <span class="hidden sm:inline" aria-hidden="true">·</span>
        {countdown}
      {/if}
      {#if noPlanner && !stale}
        <span class="hidden 2xl:inline">· sin planificador</span>
      {/if}
    </span>
    <span class="sr-only">— {sentence}</span>
  </a>
{/if}
