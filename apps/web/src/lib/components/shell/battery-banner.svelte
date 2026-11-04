<script lang="ts">
  /*
   * Low-battery notice under the topbar, on every console page. The chip shows
   * the number; this says what to do before the G1 browns out mid-stance.
   *
   * Same honest-state rules as the chip (robot-status.svelte): only a CURRENT
   * reading counts (offline keeps the last state), and the stub's invented
   * battery never warns. Not dismissable: the condition only goes away by
   * charging.
   *
   * Announced once per band, not per percent: the live region's text is the
   * band word and a fixed sentence, the number is aria-hidden (screen readers
   * have it in the chip), and the Callout is keyed on the band so warn → alert
   * inserts a fresh role=alert instead of re-reading on every 1 % tick.
   */
  import Callout from "#lib/components/callout.svelte";
  import { getRobotLive } from "#lib/robot/context.js";
  import { batteryPct, batteryWarning } from "#lib/robot/format.js";

  const live = getRobotLive();

  const raw = $derived(live.online ? live.state?.battery_pct : null);
  const band = $derived(batteryWarning(raw, live.simulated));
  const pct = $derived(batteryPct(raw));
</script>

{#key band}
  {#if band}
    <Callout
      tone={band === "alert" ? "danger" : "warn"}
      title={band === "alert" ? "Batería crítica" : "Batería baja"}
      class="shrink-0"
    >
      <span aria-hidden="true">{pct} %.</span>
      {band === "alert"
        ? "Sentá o colgá el robot ahora y cargalo: puede apagarse y caer."
        : "Cargá el robot ya, antes de seguir."}
    </Callout>
  {/if}
{/key}
