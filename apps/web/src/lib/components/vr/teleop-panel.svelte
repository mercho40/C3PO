<!--
  The teleop stream and the arm mirror.

  "Activar espejo" used to be `disabled={!vrActive}` with no explanation, and
  otherwise pressable whatever the bridge thought — so the operator learned the
  arm path was switched off from the bridge's English exception, wrapped in
  Spanish, after the press. The status frame already carried `arm.enabled_by_env`
  and `arm.sim_mode`; `armReadiness` reads them and the reason sits next to the
  button. A static footnote that always claimed rt/arm_sdk "viene
  deshabilitado" — stale the moment someone set TELEOP_ARM_ENABLED=1 — is gone
  for the same reason: the live state says it now.
-->
<script lang="ts">
  import { Hand, Link2, Link2Off } from "@lucide/svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import Callout from "#lib/components/callout.svelte";
  import DetailsNote from "./details-note.svelte";
  import { armReadiness, handsLabel } from "#lib/robot/vr-logic.js";
  import type { VrControl } from "#lib/robot/vr-control.svelte.js";
  import { cn } from "#lib/utils.js";

  let { ctl }: { ctl: VrControl } = $props();

  const status = $derived(ctl.teleopStatus);
  const readiness = $derived(
    armReadiness({
      streaming: ctl.streaming,
      vrActive: ctl.vrActive,
      status,
    }),
  );
  const hands = $derived(handsLabel(status?.hands));
  // Requested is not engaged. The button used to turn cyan on the press, while
  // the bridge was refusing the arms (`arm_error`) or still ramping — cyan for
  // arms nothing was driving. Only the bridge's own `arm.engaged` lights it.
  const armsEngaged = $derived(
    ctl.armsRequested && status?.arm.engaged === true,
  );
  const readinessTone = $derived(
    readiness.kind === "ready" || readiness.kind === "engaged"
      ? "text-ink-dim"
      : readiness.kind === "unknown"
        ? "text-ink-mute"
        : "text-warn",
  );
</script>

<section
  class="flex flex-col gap-4 panel p-5"
  aria-labelledby="vr-teleop-title"
>
  <h2 id="vr-teleop-title" class="eyebrow">Espejo de brazos (teleoperación)</h2>

  <div class="flex flex-wrap items-center gap-3">
    {#if !ctl.streaming}
      <!-- Locked during an observe-only session. Opening this socket is
           exactly what makes the headset a commander. -->
      <Button
        variant="outline"
        size="lg"
        class="min-h-12 gap-2 px-4"
        disabled={ctl.viewOnly}
        onclick={() => ctl.connectStream()}
      >
        <Link2 class="size-4" aria-hidden="true" /> Conectar puente
      </Button>
      {#if ctl.viewOnly}
        <span class="readout">
          Sesión en modo solo vista — salí de VR para poder comandar.
        </span>
      {/if}
    {:else}
      <Button
        variant="outline"
        size="lg"
        class="min-h-12 gap-2 px-4"
        onclick={() => ctl.disconnectStream()}
      >
        <Link2Off class="size-4" aria-hidden="true" /> Desconectar
      </Button>
      <!-- Cyan only while the bridge reports the arms engaged: they are then
           driven under power, which is the one thing cyan means here. Pressed
           but not (yet) engaged says so, uncoloured. Turning it OFF is never
           disabled. -->
      <Button
        variant="outline"
        size="lg"
        aria-pressed={ctl.armsRequested}
        aria-describedby="vr-arm-readiness"
        disabled={!ctl.armsRequested && !readiness.canEngage}
        onclick={() => ctl.toggleArms()}
        class={cn(
          "min-h-12 gap-2 px-4",
          armsEngaged &&
            "border-cyan bg-cyan/15 text-cyan hover:bg-cyan/20 hover:text-cyan",
        )}
      >
        <Hand class="size-4" aria-hidden="true" />
        {armsEngaged
          ? "Espejo activo · soltar"
          : ctl.armsRequested
            ? "Espejo pedido · soltar"
            : "Activar espejo"}
      </Button>
    {/if}
    <span class="readout" role="status">
      {#if ctl.teleopState === "connecting"}
        Conectando…
      {:else if ctl.teleopState === "stalled"}
        <span class="text-warn">
          Sin respuesta del puente — {ctl.teleopDetail || "el enlace se colgó"}.
          El robot ya se detuvo solo. Reconectá.
        </span>
      {:else if ctl.streaming}
        Conectado{ctl.handsVisible ? " · manos detectadas" : ""}
      {:else if ctl.teleopState === "error" || ctl.teleopDetail}
        {ctl.teleopDetail || "Desconectado"}
      {:else}
        Desconectado
      {/if}
    </span>
  </div>

  {#if ctl.streaming}
    <p id="vr-arm-readiness" class="text-sm {readinessTone}">
      {readiness.text}
    </p>

    <dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
      <div>
        <dt class="eyebrow">Cuadros</dt>
        <dd class="readout">{status?.frames_received ?? 0}</dd>
      </div>
      <div>
        <dt class="eyebrow">Calibración</dt>
        <dd class="readout">
          {status?.calibrated ? `${status.arm_length_m} m` : "pendiente"}
        </dd>
      </div>
      <div>
        <dt class="eyebrow">Brazos</dt>
        <dd class="readout">
          {status?.arm.engaged ? `peso ${status.arm.weight}` : "sueltos"}
        </dd>
      </div>
      <div>
        <dt class="eyebrow">Manos</dt>
        <dd class="readout">
          {#if hands.known}{hands.text}{:else}<span lang="en">{hands.text}</span
            >{/if}
        </dd>
      </div>
    </dl>
  {/if}

  {#if status?.stopped_by_estop}
    <Callout tone="danger" title="Detenido por PARAR">
      El puente cortó el movimiento. Soltá los controles y la cabeza al centro,
      y mantenelos así <strong>un segundo completo</strong> para volver a habilitarlo.
      Soltar y volver a apretar en el acto no alcanza: es justo lo que uno hace por
      reflejo al frenar de golpe, y no puede ser lo que reanuda al robot. Reconectar
      tampoco lo borra — la parada sigue en pie hasta que alguien la libera acá.
    </Callout>
  {/if}

  {#if status?.deadman_tripped}
    <p role="status" class="text-sm text-warn">
      Hombre muerto del puente: se mantuvo el movimiento demasiado tiempo. Soltá
      todo y volvé a empezar.
    </p>
  {/if}

  {#if ctl.streaming && ctl.armsRequested && status?.arm_error}
    <Callout tone="warn" title="El puente no tomó los brazos">
      <span lang="en">{status.arm_error}</span>
    </Callout>
  {/if}

  {#if ctl.vrSupported && !ctl.handTrackingSupported}
    <p class="text-sm text-ink-mute">
      Este navegador no expone seguimiento de manos. En el Quest hay que
      habilitarlo en Ajustes → Movimiento.
    </p>
  {/if}

  <DetailsNote>
    <p>
      Los brazos del robot copian la dirección y la extensión de los tuyos — no
      la posición exacta en metros, porque el puente no tiene el URDF del G1
      (ver
      <code>bridge/teleop/retarget.py</code>).
    </p>
    <p>
      <code>rt/arm_sdk</code> solo se habilita con
      <code>TELEOP_ARM_ENABLED=1</code> en el puente, después de verificar los
      signos de cada articulación con <code>scripts/arm_sign_check.py</code>, al
      lado del robot. Los dedos siguen apagados hasta resolver qué manos tiene
      puestas este robot (<code>scripts/hand_probe.py</code>).
    </p>
  </DetailsNote>
</section>
