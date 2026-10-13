<script lang="ts">
  /*
   * "Escuchando", on every console page, while the voice loop runs.
   *
   * A running loop is a privacy state — speech near the robot becomes a
   * conversational turn, and clear physical requests may call robot tools — and
   * once it was started from the dashboard, nothing on any other page said the
   * robot was still listening. Read from the layout's one VoiceLoopControl, so
   * it agrees with /voice by construction.
   *
   * Not cyan: cyan means "moving under power" and nothing else.
   */
  import { Mic } from "@lucide/svelte";
  import { resolve } from "$app/paths";
  import { getVoiceLoop } from "$lib/robot/context";

  const voice = getVoiceLoop();

  const ours = $derived(voice.canControl);
  // The status poll is failing: `running` is the last thing we heard, not now.
  const stale = $derived(voice.reason !== null);
  // What is announced: that the robot is listening, and who owns the loop.
  // Nothing that flickers — the stale suffix lives only in the link's title
  // and accessible name. It used to be part of the live text, so every time
  // the status poll failed or recovered the whole sentence was read again.
  const announcement = $derived(
    ours
      ? "El robot está escuchando"
      : "El robot está escuchando — voz en uso por otro operador",
  );
  const sentence = $derived(
    announcement +
      (stale
        ? " (último estado conocido; no se pudo leer el estado de voz)"
        : ""),
  );
</script>

<!-- Persistent, so a loop started elsewhere (another operator, the agent) is
     announced when it starts rather than only seen. -->
<span role="status" class="sr-only">{voice.running ? announcement : ""}</span>

{#if voice.running}
  <a
    href={resolve("/voice")}
    title={sentence}
    class={[
      "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-accent-edge bg-wash px-2 text-xs text-ink-dim transition-colors hover:bg-wash-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none focus-visible:ring-inset sm:px-2.5",
      stale && "opacity-70",
    ]}
  >
    <Mic class="size-3.5 shrink-0 text-peri" aria-hidden="true" />
    <span class="sr-only sm:not-sr-only sm:whitespace-nowrap">
      {ours ? "Escuchando" : "Voz en uso"}
    </span>
    {#if !ours}
      <span class="sr-only">por otro operador</span>
    {/if}
    {#if stale}
      <span aria-hidden="true">?</span>
      <span class="sr-only">
        (último estado conocido; no se pudo leer el estado de voz)</span
      >
    {/if}
  </a>
{/if}
