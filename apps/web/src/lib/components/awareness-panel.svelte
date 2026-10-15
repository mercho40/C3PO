<!--
  What the robot sees and hears, for the operator.

  THE ONE THING THIS PANEL MUST NEVER DO is render "no objects" as reassurance.
  D7's whole contract is that an empty scene and an unobserved scene are
  different facts, so when the detector is offline this shows the warning
  instead of an empty list — and the warning is the bridge's own words, not a
  string invented here (marked `lang="en"`, because they are English).

  The second thing: a scene that is no longer current must not look current.
  On a 503 the bridge is up but could not build a snapshot; the last scene is
  kept so the operator can still read it, but muted under "Última escena
  conocida", and the reason is always shown — it used to render only on a
  transport failure, so a 503 left a frozen scene looking live (or "Waiting
  for the first snapshot…" forever).

  Ages and staleness come from the robot as reported. Nothing is recomputed
  against the browser's clock: a console that disagrees with the robot about how
  old a reading is gives the operator no way to tell which one is wrong.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { resolve } from "$app/paths";
  import { ChevronRight, Square } from "@lucide/svelte";
  import Callout from "#lib/components/callout.svelte";
  import { Awareness } from "#lib/robot/awareness.svelte.js";
  import { formatAge } from "#lib/robot/format.js";
  import {
    FREE_SPACE_SECTORS,
    bearingWords,
    formatConfidence,
    formatRange,
    sceneNote,
    sourceName,
    sourceTone,
    sourceWord,
    type SourceTone,
  } from "#lib/robot/awareness-copy.js";

  let {
    section = "all",
  }: {
    /** Which half to render. /voice shows only what the robot hears. */
    section?: "all" | "sees" | "hears";
  } = $props();

  // Only the reads this panel draws: /voice mounts it with section="hears",
  // and that page fetching the D7 scene every second was perception work on
  // the Jetson for nothing. `section` is fixed for a mount (no page switches
  // it), so the poller is built for it once.
  // svelte-ignore state_referenced_locally
  const awareness = new Awareness(section);

  onMount(() => {
    awareness.start();
    return () => awareness.stop();
  });

  const scene = $derived(awareness.surroundings);
  // The scene on screen is the current world only when the last poll built it.
  const current = $derived(!awareness.degraded && !awareness.unreachable);
  const freeSpace = $derived(
    FREE_SPACE_SECTORS.map(([key, name]) => ({
      name,
      value: scene?.free_space?.[key],
    })).filter((s) => s.value != null),
  );

  const PILL: Record<SourceTone, string> = {
    ok: "border-ok/30 text-ok",
    warn: "border-warn/30 text-warn",
    danger: "border-danger/30 text-danger-soft",
    neutral: "border-hairline-strong text-ink-mute",
  };

  const voice = $derived(awareness.voice);
</script>

<div class="@container">
  <div class={["grid gap-4", section === "all" && "@3xl:grid-cols-2"]}>
    {#if section !== "hears"}
      <section class="flex min-w-0 flex-col gap-3 panel p-5">
        <div
          class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-2"
        >
          <div class="flex items-baseline gap-2">
            <h2 class="eyebrow">Ve</h2>
            {#if scene?.report_age_s != null}
              <span class="readout">escena {formatAge(scene.report_age_s)}</span
              >
            {/if}
          </div>
          {#if scene}
            <ul
              class="flex flex-wrap gap-1.5"
              aria-label="Fuentes de percepción"
            >
              {#each Object.entries(scene.sources ?? {}) as [name, status] (name)}
                <li
                  class="rounded-full border px-2 py-0.5 readout {PILL[
                    sourceTone(status, current)
                  ]}"
                >
                  {sourceName(name)}: {current
                    ? sourceWord(status)
                    : "sin dato"}
                </li>
              {/each}
            </ul>
          {/if}
        </div>

        {#if awareness.reason}
          <Callout tone="warn">
            {awareness.reason}
            {#if awareness.bridgeReason}
              <span class="readout text-ink-dim">
                Puente: <span lang="en">{awareness.bridgeReason}</span></span
              >
            {/if}
          </Callout>
        {/if}

        {#if !scene}
          {#if !awareness.reason}
            <p class="text-sm text-ink-mute">Esperando la primera escena…</p>
          {/if}
        {:else}
          <!-- Not dimmed with opacity: that took the 11 px readouts below
               contrast. The heading says it is history, in words, and the
               values drop to the muted ink. -->
          <div class="flex flex-col gap-3">
            {#if !current}
              <p class="eyebrow text-ink-dim">
                {awareness.degraded
                  ? "Última escena conocida — el puente no pudo armar una nueva"
                  : "Última escena conocida — sin respuesta del puente"}
              </p>
            {/if}

            <!--
              Notes BEFORE objects, deliberately. They carry "detection is
              offline, this is not an empty scene" — which an operator must read
              before, not after, concluding the way is clear. The bridge already
              declares any truncation here ("N more object(s) detected but not
              listed"), so the panel does not add its own line for it.
            -->
            <!-- Keyed by position: the bridge can repeat a sentence, and a
                 duplicate key throws in production. Known sentences lead in
                 Spanish with the bridge's English underneath; unknown ones
                 stay verbatim (awareness-copy.ts). -->
            {#each scene.notes ?? [] as raw, i (i)}
              {@const note = sceneNote(raw)}
              <div class="flex flex-col gap-0.5">
                {#if note.es}
                  <p class="text-sm text-warn">{note.es}</p>
                  <p class="readout break-words" lang="en">{note.en}</p>
                {:else}
                  <p class="text-sm text-warn" lang="en">{note.en}</p>
                {/if}
              </div>
            {/each}

            {#if awareness.objects.length > 0}
              <ul class="flex flex-col gap-1.5 text-sm">
                <!-- Keyed by position. The old key concatenated label, bearing
                     and range — "cup"+1+"2.5" and "cup"+12+".5" collide, and
                     two identical detections always did — and Svelte throws
                     each_key_duplicate on that in production. The list is
                     re-sorted every poll, so identity buys nothing here. -->
                {#each awareness.objects as obj, i (i)}
                  <li
                    class="flex flex-wrap items-baseline justify-between gap-x-3"
                  >
                    <span
                      class={[
                        "font-medium",
                        current ? "text-ink" : "text-ink-mute",
                      ]}
                      lang="en">{obj.label}</span
                    >
                    <span
                      class="flex flex-wrap items-baseline gap-x-2 text-ink-dim"
                    >
                      <span class="font-mono"
                        >{formatRange(obj.range_m)} · {bearingWords(
                          obj.bearing_deg,
                        )}</span
                      >
                      {#if formatConfidence(obj.confidence)}
                        <span class="readout"
                          >conf. {formatConfidence(obj.confidence)}</span
                        >
                      {/if}
                      {#if obj.age_s != null}
                        <span class="readout">{formatAge(obj.age_s)}</span>
                      {/if}
                    </span>
                  </li>
                {/each}
              </ul>
            {:else if awareness.detectorOnline}
              <!-- Only sayable when the detector is actually running. -->
              <p class="text-sm text-ink-mute">No se detecta nada ahora.</p>
            {/if}

            {#if freeSpace.length > 0}
              <div class="flex flex-col gap-1 border-t border-hairline pt-2.5">
                <h3 class="eyebrow">Espacio libre</h3>
                <dl class="flex flex-wrap gap-x-4 gap-y-1">
                  {#each freeSpace as s (s.name)}
                    <div class="flex items-baseline gap-1.5">
                      <dt class="text-xs text-ink-mute">{s.name}</dt>
                      <dd
                        class={[
                          "font-mono text-sm",
                          current ? "text-ink" : "text-ink-mute",
                        ]}
                      >
                        {formatRange(s.value)}
                      </dd>
                    </div>
                  {/each}
                </dl>
              </div>
            {/if}

            {#if (scene.landmarks?.length ?? 0) > 0}
              <div class="flex flex-col gap-1 border-t border-hairline pt-2.5">
                <h3 class="eyebrow">Lugares guardados</h3>
                <ul class="flex flex-col gap-1 text-sm">
                  {#each scene.landmarks ?? [] as lm, i (i)}
                    <li
                      class="flex flex-wrap items-baseline justify-between gap-x-3"
                    >
                      <span class={current ? "text-ink" : "text-ink-mute"}
                        >{lm.label}</span
                      >
                      <span class="flex items-baseline gap-2 text-ink-dim">
                        <span class="font-mono"
                          >{formatRange(lm.range_m)} · {bearingWords(
                            lm.bearing_deg,
                          )}</span
                        >
                        {#if lm.age_s != null}
                          <span class="readout">{formatAge(lm.age_s)}</span>
                        {/if}
                      </span>
                    </li>
                  {/each}
                </ul>
              </div>
            {/if}
          </div>
        {/if}
      </section>
    {/if}

    {#if section !== "sees"}
      <section class="flex min-w-0 flex-col gap-3 panel p-5">
        <div
          class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-2"
        >
          <div class="flex items-baseline gap-2">
            <h2 class="eyebrow">Escucha</h2>
            {#if voice}
              <span
                class="rounded-full border border-hairline-strong px-2 py-0.5 readout text-ink-dim"
              >
                {voice.always_listening
                  ? "micrófono siempre abierto"
                  : "pulsar para hablar · L1+L2"}
              </span>
            {/if}
          </div>
          {#if section === "all"}
            <a
              href={resolve("voice")}
              class="inline-flex min-h-8 items-center gap-0.5 rounded text-xs text-ink-mute transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
            >
              Control por voz
              <ChevronRight class="size-3.5" aria-hidden="true" />
            </a>
          {/if}
        </div>

        {#if awareness.voiceReason}
          <Callout tone="warn">
            {awareness.voiceReason}
            {#if awareness.voiceBridgeReason}
              <span class="readout text-ink-dim">
                Puente: <span lang="en">{awareness.voiceBridgeReason}</span
                ></span
              >
            {/if}
          </Callout>
        {/if}

        {#if !voice}
          {#if !awareness.voiceReason}
            <p class="text-sm text-ink-mute">
              {awareness.loaded
                ? "Sin telemetría de voz."
                : "Esperando la telemetría de voz…"}
            </p>
          {/if}
        {:else}
          {#if voice.error}
            <p class="text-sm text-danger-soft" role="alert">
              El stack de voz informa un error:
              <span lang="en">{voice.error}</span>
            </p>
          {/if}

          {#if !voice.mic_ever_open}
            <!--
              The most important sentence in this panel. With a push-to-talk
              mic, an empty transcript almost always means nobody held the
              button — NOT that the room was silent. An operator who reads it
              the other way concludes the robot ignored someone. With an
              always-on mic the same silence means something else entirely: no
              audio is arriving at all.
            -->
            <p class="text-sm text-warn">
              {#if voice.always_listening}
                El micrófono está siempre abierto, pero todavía no llegó audio.
                Revisá el micrófono del robot (fuente: <span class="font-mono"
                  >{voice.audio_source}</span
                >).
              {:else}
                El robot todavía no pudo escuchar nada. Su micrófono solo se
                abre mientras alguien mantiene <strong>L1+L2</strong> en el control:
                esto no es silencio en la sala.
              {/if}
            </p>
          {:else if voice.seconds_since_audio != null}
            <span class="readout"
              >último audio {formatAge(voice.seconds_since_audio)}</span
            >
          {/if}

          {#if voice.recent.length > 0}
            <ul class="flex flex-col gap-1.5 text-sm">
              {#each voice.recent
                .slice(-8)
                .reverse() as item, i (item.text + item.age_s + i)}
                <li class="flex items-baseline justify-between gap-3">
                  <span
                    class={[
                      "min-w-0 break-words",
                      item.kind === "stop"
                        ? "font-medium text-danger-soft"
                        : "text-ink",
                    ]}
                  >
                    {#if item.kind === "stop"}
                      <Square
                        class="mr-1 inline size-3 align-[-1px]"
                        aria-hidden="true"
                      /><span class="sr-only">Frase de parada: </span>
                    {/if}
                    {item.text}
                  </span>
                  <span class="shrink-0 readout">{formatAge(item.age_s)}</span>
                </li>
              {/each}
            </ul>
          {:else if voice.mic_ever_open}
            <p class="text-sm text-ink-mute">Nadie habló últimamente.</p>
          {/if}
        {/if}
      </section>
    {/if}
  </div>
</div>
