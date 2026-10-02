<!--
  What the robot is doing, and the way into the headset — before the session.

  "Entrar en VR" is this page's reason to exist and used to sit in the fifth
  panel, under a full-width camera. It is the first thing now, and the one `cta`
  on the page. Once a session starts this collapses into the in-overlay strip
  (`vr-strip.svelte`), so in the headset the session controls are at the top
  instead of below a column of opaque panels.
-->
<script lang="ts">
  import { Glasses } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import Callout from "$lib/components/callout.svelte";
  import DetailsNote from "./details-note.svelte";
  import { LOAD_TEXT, type PostureInfo } from "$lib/robot/posture";
  import { teleopShort } from "$lib/robot/vr-logic";
  import type { VrControl } from "$lib/robot/vr-control.svelte";

  let {
    ctl,
    posture,
    onenter,
  }: {
    ctl: VrControl;
    posture: PostureInfo;
    onenter: () => void;
  } = $props();
</script>

<section
  class="flex flex-col gap-4 panel-hero p-5"
  aria-labelledby="vr-hero-title"
>
  <div class="flex flex-wrap items-start justify-between gap-4">
    <div class="flex min-w-0 flex-col gap-1">
      <h2 id="vr-hero-title" class="eyebrow">Postura</h2>
      <p class="stamp text-2xl {LOAD_TEXT[posture.load]}" role="status">
        {posture.label}
      </p>
      <p class="max-w-[52ch] text-sm text-ink-mute">{posture.detail}</p>
    </div>

    <div class="flex flex-col items-start gap-2 sm:items-end">
      {#if ctl.xrProbe === "checking"}
        <Button size="lg" variant="outline" disabled class="min-h-12 px-5">
          Verificando WebXR…
        </Button>
      {:else if ctl.vrSupported}
        <Button
          size="lg"
          class="min-h-12 gap-2 cta px-5"
          disabled={ctl.enteringVr}
          onclick={onenter}
        >
          <Glasses class="size-4" aria-hidden="true" />
          {ctl.enteringVr ? "Esperando al visor…" : "Entrar en VR"}
        </Button>
      {:else}
        <p class="max-w-[38ch] text-sm text-ink-mute sm:text-right">
          WebXR no está disponible en este navegador. Abrí esta página desde el
          navegador del Quest para entrar en VR; el resto funciona igual.
        </p>
      {/if}
      <span class="readout"
        >Puente de teleoperación: {teleopShort(ctl.teleopState)}</span
      >
    </div>
  </div>

  {#if ctl.vrSupported && !ctl.arSupported}
    <p class="text-sm text-ink-mute">
      Este visor no reporta passthrough (<code>immersive-ar</code>). Vas a
      entrar en VR puro, donde el overlay con estos controles puede no verse.
    </p>
  {/if}

  {#if ctl.vrError}
    <Callout tone="danger" title="No se pudo entrar en VR">
      {ctl.vrError.text}
      {#if ctl.vrError.raw}
        <span class="mt-1 block readout"
          ><span lang="en">{ctl.vrError.raw}</span></span
        >
      {/if}
    </Callout>
  {/if}

  <DetailsNote summary="Cómo se gira con la cabeza">
    <p>
      Adentro de VR, girá la cabeza más de 6° respecto del frente calibrado para
      que el robot empiece a girar; a más ángulo, más rápido, hasta 30°. Se
      combina con los botones de caminar. «Recentrar» toma tu dirección actual
      como el nuevo frente.
    </p>
  </DetailsNote>
</section>
