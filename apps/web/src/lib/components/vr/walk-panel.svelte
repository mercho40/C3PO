<!--
  Hold-to-walk.

  The gestures section had a posture gate and an untested badge; this one had
  neither, which is exactly the "press Adelante, get nothing" scenario the
  bring-up panel exists to prevent. walk_velocity needs a walk program (FSM
  500/501) and is `works_real=False` in the bridge — both are said here now,
  above the buttons, before the press.

  It also used to tell a non-admin that walking "SÍ funciona" through the
  teleop stream — true only once the stream is connected. By default the
  buttons go through POST /skills/walk_velocity/invoke, which is admin-only, so
  following the banner produced a 403. Without admin the buttons are disabled
  until the stream is up, and the way to bring it up is right here.
-->
<script lang="ts">
  import { ArrowDown, ArrowUp, Link2 } from "@lucide/svelte";
  import { Badge } from "#lib/components/ui/badge/index.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import Callout from "#lib/components/callout.svelte";
  import HoldButton from "./hold-button.svelte";
  import OutcomeLine from "./outcome-line.svelte";
  import type { PostureGate } from "#lib/robot/vr-logic.js";
  import {
    MAX_HOLD_S,
    WALK_VX,
    type VrControl,
  } from "#lib/robot/vr-control.svelte.js";

  let {
    ctl,
    gate,
    postureLabel,
    isAdmin,
    badge,
  }: {
    ctl: VrControl;
    gate: PostureGate;
    postureLabel: string;
    isAdmin: boolean;
    badge: "untested" | "unknown" | null;
  } = $props();

  const needsStream = $derived(!isAdmin && !ctl.bridgeHolds);
  // PARAR is still going out: no press may race it (`VrControl.startWalking`
  // refuses too — this is what makes the refusal visible before the press).
  const blocked = $derived(needsStream || ctl.stopInFlight);
  const dirLabel = $derived(ctl.walking === "forward" ? "adelante" : "atrás");
  const notice = $derived(ctl.currentWalkNotice);
</script>

<section class="flex flex-col gap-4 panel p-5" aria-labelledby="vr-walk-title">
  <div class="flex flex-wrap items-center justify-between gap-2">
    <h2 id="vr-walk-title" class="eyebrow">Caminar</h2>
    {#if badge}
      <Badge variant="outline" class="text-2xs text-ink-mute">
        {badge === "untested" ? "Sin probar en real" : "Sin verificar"}
      </Badge>
    {/if}
  </div>

  {#if needsStream}
    <Callout
      tone="warn"
      title="Sin admin, caminar necesita el puente de teleoperación"
    >
      Por la API, caminar requiere una cuenta admin. El puente de teleoperación
      no pasa por esa puerta: conectalo y estos botones mueven al robot por ahí.
      {#snippet action()}
        <Button
          variant="outline"
          size="lg"
          class="min-h-12 gap-2 px-4"
          onclick={() => ctl.connectStream()}
        >
          <Link2 class="size-4" aria-hidden="true" />
          {ctl.teleopState === "connecting"
            ? "Conectando…"
            : "Conectá el puente para caminar"}
        </Button>
      {/snippet}
    </Callout>
  {/if}

  {#if gate === "offline"}
    <Callout tone="warn" title="Sin conexión con el robot">
      No se puede confirmar que esté en marcha. Si caminás igual, mirá al robot,
      no la pantalla.
    </Callout>
  {:else if gate === "wrong_posture"}
    <Callout tone="warn" title="Caminar necesita al robot en marcha">
      Ahora está en <strong>{postureLabel}</strong>. Hacé el paso 3 de
      <em>Puesta en marcha</em> («Habilitar marcha»); desde cualquier otro estado
      el firmware no camina. No es una falla del robot.
    </Callout>
  {/if}

  <div class="grid grid-cols-2 gap-4">
    <HoldButton
      label="Adelante"
      icon={ArrowUp}
      active={ctl.walking === "forward"}
      disabled={blocked}
      describedby="vr-walk-help"
      onstart={() => ctl.startWalking("forward")}
      onstop={() => ctl.stopWalking()}
    />
    <HoldButton
      label="Atrás"
      icon={ArrowDown}
      active={ctl.walking === "back"}
      disabled={blocked}
      describedby="vr-walk-help"
      onstart={() => ctl.startWalking("back")}
      onstop={() => ctl.stopWalking()}
    />
  </div>

  <p id="vr-walk-help" class="readout" role="status">
    {#if ctl.walking && ctl.bridgeHolds}
      <!-- The bridge picks the speed on the stream path; WALK_VX is only this
           page's own walk_velocity loop. -->
      Caminando {dirLabel} por el puente de teleoperación…
    {:else if ctl.walking}
      Manteniendo {WALK_VX} m/s {dirLabel}…{ctl.lastMoveSimulated
        ? " (simulado: el puente está en stub, el robot no se mueve)"
        : ""}
    {:else if ctl.stopInFlight}
      PARAR en curso: la marcha espera a que termine la parada.
    {:else}
      Mantené presionado (o Espacio) para caminar. Al soltar, frena solo.
    {/if}
  </p>

  <!-- The latch. After a PARAR it is only worth saying in VR, where head-yaw
       steering would otherwise resume on its own: outside the headset the next
       press is the re-arm, and pressing is what the operator does anyway. -->
  {#if ctl.deadManTripped && ctl.latchCause === "deadman"}
    <p class="text-sm text-warn" role="status">
      Hombre muerto: más de {MAX_HOLD_S} s de movimiento continuo. Soltá todo y llevá
      la cabeza al centro para rearmar.
    </p>
  {:else if ctl.deadManTripped && ctl.vrActive && !ctl.stopInFlight && !ctl.bridgeHolds}
    <p class="text-sm text-warn" role="status">
      PARAR cortó la marcha y el giro. Soltá todo y llevá la cabeza al centro (o
      tocá Recentrar) para volver a moverlo.
    </p>
  {/if}

  <!-- No role="alert": the "hace N s" in OutcomeLine changes every second, so
       an assertive region re-announced the refusal for up to a minute. The
       page's one polite region already says it once ("No camina: …"). -->
  {#if notice}
    <div class="flex flex-col gap-1 text-sm">
      <span class="text-ink-dim">
        {notice.stopFailed ? "La parada no se confirmó:" : "No camina:"}
      </span>
      <OutcomeLine stamped={notice} now={ctl.now} class="text-sm" />
      {#if notice.stopFailed}
        <span class="text-xs text-ink-mute">
          El robot igual frena solo al terminar el último pulso (1 s).
        </span>
      {/if}
    </div>
  {/if}
</section>
