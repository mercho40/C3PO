<!--
  Bring-up: damp → prepare → start_walking_waist, against the live posture.

  Without this the page was unusable on hardware and the reason was invisible.
  Walking needs the robot in a walk program, and `rt/arm_sdk` needs FSM 4 / 500 /
  501 — but /vr-control only ever *displayed* posture, it could not change it.
  So an operator already wearing the headset would press Adelante, get nothing,
  enable the arm mirror, get "FSM ... is not one of [4, 500, 501]", and have to
  take the headset off to fix it somewhere else.

  The steps now say where the robot is on the sequence (done / current / next)
  instead of looking identical whatever the FSM. The "Estado actual" marker is
  coloured by the LIVE load, not by the step: «Habilitar marcha» used to be
  cyan for any walk program, a robot standing still at zero stick included,
  and cyan means "moving under power" in this console.

  One column until the panel itself is wide enough for three (a container
  query, not a viewport one: on the two-column desktop layout this panel is
  half the page). Three narrow cards stretched to the tallest one — step 3,
  with its hold instruction — left the other two with large empty bottoms.
-->
<script lang="ts">
  import SkillTile from "./skill-tile.svelte";
  import LockLine from "./lock-line.svelte";
  import {
    BRING_UP_STEPS,
    bringUpProgress,
    needsHoldConfirm,
  } from "$lib/robot/vr-logic";
  import type { VrControl } from "$lib/robot/vr-control.svelte";
  import type { Load } from "$lib/robot/posture";

  let {
    ctl,
    posture,
    postureLabel,
    load,
    online,
    dangerLevels,
  }: {
    ctl: VrControl;
    /** Raw FSM token from /state. */
    posture: string | null;
    /** The same posture, translated. */
    postureLabel: string;
    /** What the body is doing now (`readPosture` with measured motion). */
    load: Load;
    online: boolean;
    /** Catalogue danger level per skill (high → hold to confirm). */
    dangerLevels: Record<string, string>;
  } = $props();

  const progress = $derived(bringUpProgress(posture, online));
  const nextLabel = $derived(
    BRING_UP_STEPS.find((s) => s.name === progress.next)?.label ?? null,
  );
  const nextHint = $derived.by(() => {
    if (!online) return "Sin conexión: no se sabe en qué paso está el robot.";
    if (!progress.known)
      return `Postura «${postureLabel}»: no se puede ubicar en la secuencia.`;
    if (progress.offSequence)
      return `Siguiente: ${nextLabel} (el robot está en «${postureLabel}», fuera de la secuencia).`;
    if (nextLabel) return `Siguiente: ${nextLabel}.`;
    return "Listo: el robot está en marcha.";
  });
</script>

<section
  class="@container flex flex-col gap-4 panel p-5"
  aria-labelledby="vr-bringup-title"
>
  <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
    <h2 id="vr-bringup-title" class="eyebrow">Puesta en marcha</h2>
    <p class="text-sm text-ink-dim" role="status">{nextHint}</p>
  </div>
  <div class="grid grid-cols-1 gap-3 @2xl:grid-cols-3">
    {#each BRING_UP_STEPS as step (step.name)}
      <SkillTile
        label={step.label}
        hint={step.hint}
        mark={progress.marks[step.name]}
        markLoad={load}
        holdToConfirm={needsHoldConfirm(step.name, dangerLevels)}
        busy={ctl.inFlight[step.name] === true}
        locked={ctl.lockedOut(step.name)}
        stamped={ctl.outcomeFor(step.name)}
        now={ctl.now}
        onrun={() => ctl.runSkill(step.name, step.label, step.name)}
      />
    {/each}
  </div>
  <LockLine lock={ctl.lock} />
  <p class="readout">
    En este orden. Caminar, girar y los gestos necesitan el paso 3; el espejo de
    brazos necesita el 2 o el 3. Si algo se ignora en silencio, casi siempre es
    porque el robot no está en ninguno de esos estados.
  </p>
</section>
