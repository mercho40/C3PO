<!--
  /voice — "Control por voz".

  The voice session used to be an English card at the bottom of the dashboard,
  while the sidebar entry for it said "Pronto". This is its home now: the
  switch and what the session is doing on the left; what the robot actually
  hears (the bridge's own microphone telemetry) and past transcripts on the
  right. The topbar's listening chip reads the same shared voice loop, so the
  privacy state stays visible on every other page.

  When the loop's status cannot be read, everything that came from it reads
  as the last reading — the hero dims and labels itself, and "Decir" words its
  overlap warning as last known — never as current.

  The page title comes from the topbar (`$lib/nav.ts`).
-->
<script lang="ts">
  import AwarenessPanel from "$lib/components/awareness-panel.svelte";
  import VoiceControl from "$lib/components/voice-control.svelte";
  import SayBox from "$lib/components/voice/say-box.svelte";
  import Transcripts from "$lib/components/voice/transcripts.svelte";
  import { engineOf } from "$lib/components/voice/voice-view";
  import { getVoiceLoop } from "$lib/robot/context";

  let { data } = $props();

  const voice = getVoiceLoop();
  // Unknown until the loop has answered once — the transcript note must not
  // guess which engine is in use.
  const engine = $derived(voice.state ? engineOf(voice.state) : null);
</script>

<div class="grid gap-4 pb-2 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
  <div class="flex min-w-0 flex-col gap-4">
    <VoiceControl />

    {#if data.isAdmin}
      <SayBox
        parameters={data.sayParameters}
        error={data.sayError}
        conversationRunning={voice.running}
        conversationHeld={voice.reason !== null}
      />
    {:else}
      <!-- Not hidden entirely: an operator should know the capability exists
           and why it is not theirs, rather than wonder where it went. -->
      <section
        class="flex min-w-0 flex-col gap-2 panel p-5"
        aria-labelledby="say-locked-heading"
      >
        <h2 id="say-locked-heading" class="eyebrow">Decir</h2>
        <p class="text-sm text-ink-mute">
          Hacer hablar al robot directamente es una orden sin agente de por
          medio, así que está reservado a cuentas admin. Podés pedírselo al
          agente en el chat.
        </p>
      </section>
    {/if}
  </div>

  <div class="flex min-w-0 flex-col gap-4">
    <AwarenessPanel section="hears" />
    <Transcripts
      transcripts={data.transcripts}
      error={data.transcriptsError}
      capped={data.transcriptsCapped}
      scanned={data.transcriptsScanned}
      {engine}
    />
  </div>
</div>
