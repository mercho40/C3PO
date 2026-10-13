<!--
  What the e-stop could not fit in one word — inside the headset.

  The shell's EstopBanner carries the instructions a failed stop needs ("Usá la
  parada física") and its "Descartar". It lives under the topbar, outside the
  WebXR dom-overlay, so while the headset is on the wearer saw a red "SIN
  PUENTE" on the overlay's PARAR and nothing telling them the order had not
  gone out — and had no way to put the button back to rest. This is a compact
  copy of the same message, from the same store and the same `estopMessage`,
  rendered in the sticky bar next to that PARAR.

  NOT a live region (`live={false}`): EstopBanner is still mounted and is the
  ONE announcer for the press. Two would announce every stop twice.
-->
<script lang="ts">
  import { Button } from "$lib/components/ui/button/index.js";
  import Callout from "$lib/components/callout.svelte";
  import { estop } from "$lib/robot/estop.svelte";
  import { estopMessage } from "$lib/robot/estop";

  const message = $derived(
    estopMessage(estop.phase, estop.outcome?.code ?? null),
  );
</script>

<!-- "Parando…" / "Detenido" are already the button's own word; only what
     needs a decision is repeated here, as in the banner. -->
{#if message && message.level !== "polite"}
  <Callout
    tone={message.level === "alert" ? "danger" : "warn"}
    title={message.title}
    live={false}
  >
    {message.body}
    {#snippet action()}
      <Button
        variant="outline"
        size="lg"
        class="min-h-12 px-4"
        onclick={() => estop.dismiss()}
      >
        Descartar
      </Button>
    {/snippet}
  </Callout>
{/if}
