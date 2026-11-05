<!--
  The in-headset session strip, right under the e-stop.

  Recentrar and Salir de VR used to be 32 px buttons in the fifth panel; in the
  dom-overlay they sat below opaque panels plus a second copy of the camera the
  XR layer already draws. They are here now, 48 px, in the sticky bar with PARAR
  — together with the readings the wearer needs at a glance: mode, posture, head
  yaw, whether tracking is stale, and the stream. The latched stops are repeated
  here too, because the panels that explain them may be scrolled out of view.

  Repeated VISUALLY only. The walk and teleop panels stay mounted in VR and
  already announce these (role=status / the danger Callout's role=alert), so
  live regions here too made every latch speak twice.
-->
<script lang="ts">
  import { Crosshair, Eye, LogOut } from "@lucide/svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { LOAD_TEXT, type PostureInfo } from "#lib/robot/posture.js";
  import { teleopShort } from "#lib/robot/vr-logic.js";
  import { MAX_HOLD_S, type VrControl } from "#lib/robot/vr-control.svelte.js";

  let { ctl, posture }: { ctl: VrControl; posture: PostureInfo } = $props();
</script>

<div class="flex flex-col gap-2">
  <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
    <h1 class="stamp-quiet text-sm text-ink">Control VR</h1>
    <span class="readout text-ink-dim">
      {ctl.xrMode === "immersive-ar" ? "VR · passthrough" : "VR puro"}
    </span>
    <span class="stamp text-sm {LOAD_TEXT[posture.load]}">{posture.label}</span>
    <!-- Not a live region: the yaw changes five times a second. The stale
         flag is the part worth announcing, so only it is. -->
    <span class="readout">
      {ctl.yawDisplayDeg}° de giro<span role="status" class="text-warn"
        >{ctl.trackingStale ? " · seguimiento perdido" : ""}</span
      >
    </span>
    <span class="readout">Puente: {teleopShort(ctl.teleopState)}</span>
    {#if ctl.viewOnly}
      <!-- Said where the wearer is looking: a passive session must not be
           mistakable for one that drives. -->
      <span class="flex items-center gap-1 stamp text-sm text-warn">
        <Eye class="size-3.5" aria-hidden="true" /> Solo vista — no comanda
      </span>
    {/if}
    <div class="ms-auto flex flex-wrap gap-2">
      <Button
        variant="outline"
        size="lg"
        class="min-h-12 gap-2 px-4"
        onclick={() => ctl.recenterVr()}
      >
        <Crosshair class="size-4" aria-hidden="true" /> Recentrar
      </Button>
      <Button
        variant="outline"
        size="lg"
        class="min-h-12 gap-2 px-4"
        onclick={() => ctl.exitVr()}
      >
        <LogOut class="size-4" aria-hidden="true" /> Salir de VR
      </Button>
    </div>
  </div>

  {#if ctl.teleopStatus?.stopped_by_estop}
    <!-- The bridge's own latch: it governs while the stream holds locomotion. -->
    <p class="text-sm text-danger-soft">
      Detenido por PARAR. Soltá todo y la cabeza al centro durante un segundo
      completo para rearmar.
    </p>
  {:else if ctl.deadManTripped && ctl.latchCause === "estop" && !ctl.stopInFlight && !ctl.bridgeHolds}
    <!-- This page's latch after a PARAR: head-yaw steering stays off until a
         deliberate re-arm. -->
    <p class="text-sm text-warn">
      PARAR cortó la marcha y el giro. Soltá todo y llevá la cabeza al centro (o
      tocá Recentrar) para volver a moverlo.
    </p>
  {/if}
  {#if ctl.deadManTripped && ctl.latchCause === "deadman"}
    <p class="text-sm text-warn">
      Hombre muerto: más de {MAX_HOLD_S} s de movimiento continuo. Soltá todo y llevá
      la cabeza al centro para rearmar.
    </p>
  {:else if ctl.teleopStatus?.deadman_tripped}
    <p class="text-sm text-warn">
      Hombre muerto del puente: soltá todo y volvé a empezar.
    </p>
  {/if}
</div>
