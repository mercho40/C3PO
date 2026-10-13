<!--
  Preset gestures, plus the rest of the firmware's catalogue.

  Only five dedicated tools used to be reachable from the headset, while the
  bridge's `gesture` tool (since 8d0715a) runs any of the robot's 23 catalogued
  actions through the same verified 7106 path and releases the arm afterwards.
  "Más gestos" dispatches that tool by name.

  Badges never claim more than the catalogue does: a skill the bridge marks
  untested says so, and a skill whose flag could not be read says THAT — an
  empty catalogue used to make every badge vanish, reading as "all verified" at
  the moment the page knew least.
-->
<script lang="ts">
  import {
    Hand,
    Handshake,
    HeartHandshake,
    Music,
    PartyPopper,
    RotateCcw,
  } from "@lucide/svelte";
  import Callout from "$lib/components/callout.svelte";
  import SkillTile from "./skill-tile.svelte";
  import LockLine from "./lock-line.svelte";
  import DetailsNote from "./details-note.svelte";
  import { formatAge } from "$lib/robot/format";
  import {
    PRESETS as PRESET_SKILLS,
    gestureCaveat,
    moreGestures,
    needsHoldConfirm,
    realBadge,
    type PresetName,
    type CatalogueHealth,
    type PostureGate,
  } from "$lib/robot/vr-logic";
  import type { VrControl } from "$lib/robot/vr-control.svelte";

  let {
    ctl,
    gate,
    postureLabel,
    worksReal,
    dangerLevels,
    catalogue,
    gestureNames,
    gestureTool,
  }: {
    ctl: VrControl;
    gate: PostureGate;
    postureLabel: string;
    worksReal: Record<string, boolean>;
    /** Catalogue danger level per skill (high → hold to confirm; all low today). */
    dangerLevels: Record<string, string>;
    catalogue: CatalogueHealth;
    /** The firmware catalogue, from the bridge's schema or the mirrored list. */
    gestureNames: readonly string[];
    /** Whether the bridge exposes `gesture`; null when the catalogue is unknown. */
    gestureTool: boolean | null;
  } = $props();

  // The list itself lives in vr-logic, shared with the headset's menu; only
  // the icons are this panel's.
  const ICONS: Record<PresetName, typeof Hand> = {
    wave: Hand,
    dance: Music,
    shake_hand: Handshake,
    hug: HeartHandshake,
    clap: PartyPopper,
    release_arm: RotateCcw,
  };
  const PRESETS = PRESET_SKILLS.map((p) => ({ ...p, icon: ICONS[p.name] }));

  const more = $derived(
    moreGestures(
      gestureNames,
      PRESETS.map((p) => p.name),
    ),
  );
  const gestureBadge = $derived(realBadge(worksReal, "gesture"));
  // Every tile carries the gate's reason while it is shut — see `gestureCaveat`.
  const caveat = $derived(gestureCaveat(gate));
</script>

<section
  class="flex flex-col gap-4 panel p-5"
  aria-labelledby="vr-gestures-title"
>
  <h2 id="vr-gestures-title" class="eyebrow">Gestos preprogramados</h2>

  {#if catalogue.failed}
    <Callout tone="warn" title="No se pudo leer el catálogo de habilidades">
      Los badges dicen «Sin verificar»: no sabemos cuáles se probaron en el
      robot — <em>no</em> quiere decir que estén verificados. Revisá que el
      puente esté corriendo y el túnel abierto.
      {#if catalogue.error && catalogue.error !== "empty_catalogue"}
        <span class="mt-1 block readout"
          ><span lang="en">{catalogue.error}</span></span
        >
      {/if}
    </Callout>
  {:else if catalogue.staleAgeS !== null}
    <Callout tone="info" title="Catálogo guardado, no en vivo">
      El puente no respondió; los badges vienen de la última lectura ({formatAge(
        catalogue.staleAgeS,
      )}).
    </Callout>
  {/if}

  {#if gate === "offline"}
    <Callout tone="warn" title="Sin conexión con el robot">
      No se puede saber si está en marcha, y los gestos lo necesitan. Si tocás
      uno igual, el resultado va a aparecer en el botón.
    </Callout>
  {:else if gate === "wrong_posture"}
    <!-- Every dedicated gesture tool declares `fsm_state_in_{walk,walk_waist,
         run}`. Nothing in apps/back enforces it and the bridge deliberately
         does not pre-check, so the FIRMWARE refuses — the "go debug the robot"
         signal for a robot that is simply standing still. -->
    <Callout tone="warn" title="Los gestos necesitan al robot en marcha">
      El firmware los rechaza (error 7302) desde cualquier otro estado — ahora
      está en <strong>{postureLabel}</strong>. Hacé <em>Puesta en marcha</em>
      (pasos 1 → 2 → 3) y después probá de nuevo. No es una falla del robot.
    </Callout>
  {/if}

  <!-- Only while something holds the lock. The line reserves its height for
       the bring-up panel, whose copy is a persistent live region; this copy is
       silent (`announce={false}`), so it can come and go — an always-present
       empty line plus two flex gaps was the large hole above the tiles. -->
  {#if ctl.lock}
    <LockLine lock={ctl.lock} announce={false} />
  {/if}

  <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
    {#each PRESETS as preset (preset.name)}
      <SkillTile
        layout="gesture"
        label={preset.label}
        icon={preset.icon}
        badge={realBadge(worksReal, preset.name)}
        holdToConfirm={needsHoldConfirm(preset.name, dangerLevels)}
        {caveat}
        busy={ctl.inFlight[preset.name] === true}
        locked={ctl.lockedOut(preset.name)}
        stamped={ctl.outcomeFor(preset.name)}
        now={ctl.now}
        onrun={() => ctl.runSkill(preset.name, preset.label, preset.name)}
      />
    {/each}
  </div>

  <DetailsNote
    summary={`Más gestos (${more.length})`}
    summaryClass="text-sm text-ink-dim"
  >
    <p>
      El resto del catálogo del firmware, por la misma ruta (7106) que
      «Saludar». Cada gesto suelta el brazo solo al terminar. La ruta está
      probada en el robot; la mayoría de los gestos individuales todavía no se
      vieron ejecutarse.
    </p>
    {#if gestureTool === false}
      <Callout
        tone="warn"
        title="Este puente no expone la herramienta «gesture»"
      >
        Es una versión anterior del puente: estos botones van a responder
        «Habilidad desconocida». Actualizá el puente en el robot.
      </Callout>
    {/if}
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
      {#each more as g (g.name)}
        {@const key = `gesture:${g.name}`}
        <SkillTile
          layout="compact"
          label={g.label}
          labelLang={g.known ? undefined : "en"}
          hint={g.hint}
          badge={gestureBadge}
          holdToConfirm={needsHoldConfirm("gesture", dangerLevels)}
          {caveat}
          busy={ctl.inFlight[key] === true}
          locked={ctl.lockedOut(key)}
          stamped={ctl.outcomeFor(key)}
          now={ctl.now}
          onrun={() => ctl.runSkill(key, g.label, "gesture", { name: g.name })}
        />
      {/each}
    </div>
  </DetailsNote>
</section>
