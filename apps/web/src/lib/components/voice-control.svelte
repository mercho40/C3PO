<!--
  The voice session's switch and status — /voice's hero panel.

  STARTING IT IS A DECISION, NOT A TOGGLE. While a session runs, speech near
  the robot becomes conversational turns, and a clear physical request can move
  it. So the control is two explicit verbs ("Iniciar" / "Terminar"), the privacy
  consequence is one sentence away from the button, and the console never shows
  "running" because a button was pressed — `back`'s reply is the state
  (`voice-loop.ts`).

  "RUNNING" IS NOT "HEARING". The mic caveat appears only while a session runs,
  worded by what the bridge says the microphone is (`micWarning`), so it can no
  longer contradict the Oye panel beside it when nothing is running.

  Counters are the loop's own, as it reported them, per engine (`counters`).
  Under Realtime there is no stops counter at all: spoken stop phrases do
  nothing there, and a "0" would imply a guard that is not there. With nothing
  running they read "—": there is nothing being counted.

  NEVER FRESH WHEN IT IS NOT. A failed status poll leaves the last state on
  screen dimmed and labelled "(última lectura)"; a robot link that is down
  turns "Activa" amber and says which link (`sessionStatus`) — back can call a
  session running while the robot it listens through is unreachable.

  ONE LIVE REGION: the status line. It changes on connect, on, off, error and
  held — not on every Escuchando ↔ Hablando turn, which used to be announced
  every few seconds. The explanatory callouts are not live either.

  Shared instance from the protected layout (the topbar's listening chip reads
  the same one) — never construct a second.
-->
<script lang="ts">
  import { ExternalLink, Mic, MicOff, X } from "@lucide/svelte";
  import { resolve } from "$app/paths";
  import Callout from "$lib/components/callout.svelte";
  import { getRobotLive, getVoiceLoop } from "$lib/robot/context";
  import { LINK } from "$lib/robot/link-copy";
  import {
    ENGINE_LABEL,
    counters,
    countersScope,
    engineOf,
    lastTurnMetrics,
    micWarning,
    ownership,
    phaseView,
    sessionStatus,
    type StatusTone,
  } from "$lib/components/voice/voice-view";

  const voice = getVoiceLoop();
  const live = getRobotLive();

  const s = $derived(voice.state);
  const engine = $derived(engineOf(s));
  const owner = $derived(ownership(s));
  const phase = $derived(phaseView(s));
  const counts = $derived(counters(s));
  const scope = $derived(countersScope(s));
  const metrics = $derived(lastTurnMetrics(s));
  // A status poll that failed leaves the last state on screen; it is dimmed
  // and labelled rather than presented as current.
  const held = $derived(voice.reason !== null && s !== null);
  // The mic caveat is a claim about the present — not made from a held state.
  const mic = $derived(held ? null : micWarning(s));
  // The robot link, in the shared vocabulary. A 401 is a session problem the
  // layout already handles; it says nothing about whether the robot hears.
  const robotLink = $derived(
    live.online || live.problem === "expired"
      ? null
      : LINK[live.problem ?? "offline"],
  );
  const status = $derived(
    sessionStatus({ state: s, reason: voice.reason, robotLink }),
  );
  const TONE: Record<StatusTone, string> = {
    ok: "text-ok",
    warn: "text-warn",
    danger: "text-danger-soft",
    mute: "text-ink-mute",
  };
</script>

<section
  class="flex min-w-0 flex-col gap-5 panel-hero p-5 sm:p-6"
  aria-labelledby="voice-session-heading"
>
  <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
    <h2 id="voice-session-heading" class="eyebrow">Conversación de voz</h2>
    <p
      class="flex items-center gap-1.5 text-sm font-medium {TONE[status.tone]}"
      role="status"
      aria-live="polite"
    >
      {#if s?.running}
        <Mic class="size-4 shrink-0" aria-hidden="true" />
      {:else}
        <MicOff class="size-4 shrink-0" aria-hidden="true" />
      {/if}
      {status.text}
    </p>
  </div>

  <p class="text-sm leading-relaxed text-ink-dim">
    Mientras esté activa, lo que se diga cerca del robot se vuelve un turno de
    conversación, y un pedido físico claro puede moverlo.
    {#if engine === "realtime"}
      El audio se envía a OpenAI Realtime.
    {/if}
  </p>

  {#if voice.commandError}
    <Callout tone="danger" title="No se pudo cambiar la conversación">
      {voice.commandError}
      {#snippet action()}
        <button
          type="button"
          onclick={() => voice.dismissError()}
          class="flex size-8 items-center justify-center rounded-md text-danger-soft transition-colors hover:bg-danger/10 focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
          aria-label="Descartar el error"
        >
          <X class="size-4" aria-hidden="true" />
        </button>
      {/snippet}
    </Callout>
  {/if}

  {#if voice.reason}
    <Callout tone="warn" title="Sin estado actual de la conversación">
      {voice.reason}
      {#if held}Lo que ves abajo es la última lectura.{/if}
    </Callout>
  {/if}

  {#if s?.running && robotLink && !held}
    <!-- Not live: the status line above already announced "Activa · …". -->
    <Callout tone="warn" title={robotLink} live={false}>
      El servidor da la conversación por activa, pero la consola no ve al robot:
      no hay forma de saber si oye o habla hasta que vuelva el enlace.
    </Callout>
  {/if}

  {#if owner === "other"}
    <Callout tone="info" title="En uso por otro operador" live={false}>
      Otro operador inició esta conversación. Solo esa persona puede terminarla
      y ver su transcripción.
    </Callout>
  {/if}

  {#if s?.running && engine === "realtime"}
    <Callout
      tone="warn"
      title="La frase de parada hablada no funciona con Realtime"
      live={false}
    >
      Decir «detener» no frena al robot en este modo. Las únicas paradas son
      PARAR (arriba, o <kbd class="font-mono">Shift+Espacio</kbd>) y la parada
      física del control remoto.
    </Callout>
  {/if}

  {#if mic}
    <Callout tone="info" live={false}>{mic}</Callout>
  {/if}

  {#if s?.lastError}
    <Callout
      tone={held ? "warn" : "danger"}
      title={held
        ? "La sesión informó un error (última lectura)"
        : "La sesión informó un error"}
    >
      <span lang="en">{s.lastError}</span>
    </Callout>
  {/if}

  <!-- The phase. A loop, not a wizard: Escuchando and Hablando alternate, so
       "done" only means "already passed through this turn". -->
  <div class={["flex flex-col gap-2", held && "opacity-60"]}>
    <h3 class="eyebrow">Fase</h3>
    <ol class="grid grid-cols-3 gap-2" aria-label="Fase de la conversación">
      {#each phase.steps as step (step.key)}
        <li
          class={[
            "flex min-w-0 items-center justify-center tile px-2 py-2 text-center text-xs font-medium",
            step.status === "active" && "border-accent-edge text-ink",
            step.status === "done" && "text-ink-dim",
            step.status === "todo" && "text-ink-mute",
          ]}
          aria-current={step.status === "active" ? "step" : undefined}
        >
          {step.label}
        </li>
      {/each}
    </ol>
  </div>

  {#if s}
    <!-- Sized by the PANEL, not the viewport: three tiles need ~27rem before
         "HERRAMIENTAS" fits, and the hero is that narrow both on a phone and
         in the two-column layout at 1024–1150 px. Below it, one row per
         counter, label left and value right. -->
    <div class={["@container flex flex-col gap-1.5", held && "opacity-60"]}>
      <dl class="grid grid-cols-1 gap-2 @min-[27rem]:grid-cols-3">
        {#each counts as c (c.label)}
          <div
            class="flex min-w-0 items-baseline justify-between gap-3 tile px-3 py-2 @min-[27rem]:flex-col @min-[27rem]:items-start @min-[27rem]:justify-start @min-[27rem]:gap-1 @min-[27rem]:py-2.5"
            title={c.hint}
          >
            <dt class="max-w-full truncate eyebrow">{c.label}</dt>
            <dd
              class={[
                "font-mono text-lg tabular-nums",
                c.value === "—" ? "text-ink-mute" : "text-ink",
              ]}
            >
              {c.value}
            </dd>
          </div>
        {/each}
      </dl>
      {#if scope}
        <p class="readout">{scope}</p>
      {/if}
    </div>

    {#if s.lastHeard || s.lastReply}
      <div class={["flex flex-col gap-3", held && "opacity-60"]}>
        {#if s.lastHeard}
          <div class="flex min-w-0 flex-col gap-1">
            <h3 class="eyebrow">Lo último que oyó</h3>
            <p class="text-sm break-words text-ink">«{s.lastHeard}»</p>
          </div>
        {/if}
        {#if s.lastReply}
          <div class="flex min-w-0 flex-col gap-1">
            <h3 class="eyebrow">Lo último que respondió</h3>
            <p class="text-sm break-words text-ink-dim">«{s.lastReply}»</p>
          </div>
        {/if}
      </div>
    {/if}

    {#if metrics.length}
      <div class={["flex flex-col gap-1.5", held && "opacity-60"]}>
        <h3 class="eyebrow">Último turno</h3>
        <dl class="flex flex-wrap gap-x-4 gap-y-1">
          {#each metrics as m (m.label)}
            <div class="flex gap-1.5">
              <dt class="readout">{m.label}</dt>
              <dd class="readout text-ink-dim">{m.value}</dd>
            </div>
          {/each}
        </dl>
      </div>
    {/if}

    <p class="readout">Motor · {ENGINE_LABEL[engine]}</p>
  {/if}

  <div class="flex flex-wrap items-center gap-2">
    {#if s?.running}
      <button
        type="button"
        disabled={voice.busy || !voice.canControl}
        onclick={() => voice.stopLoop()}
        class="inline-flex h-10 items-center justify-center rounded-lg border border-danger/40 px-4 text-sm font-medium text-danger-soft transition-colors hover:bg-danger/10 focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
        title={voice.canControl
          ? undefined
          : "Solo quien la inició puede terminarla"}
      >
        {voice.pending === "stop" ? "Terminando…" : "Terminar conversación"}
      </button>
    {:else}
      <button
        type="button"
        disabled={voice.busy}
        onclick={() => voice.startLoop()}
        class="inline-flex h-10 items-center justify-center rounded-lg cta px-4 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
      >
        {voice.pending === "start" ? "Iniciando…" : "Iniciar conversación"}
      </button>
    {/if}

    <!-- Back sends chatId to the session's owner only; anyone else's link
         would 404 and silently open a blank chat. -->
    {#if s?.chatId && owner !== "other"}
      <a
        href={resolve(`/chat?id=${encodeURIComponent(s.chatId)}`)}
        class="inline-flex h-10 items-center gap-1.5 rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
      >
        Ver transcripción
        <ExternalLink class="size-3.5" aria-hidden="true" />
      </a>
    {/if}
  </div>
</section>
