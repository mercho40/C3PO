<!--
  Calls the bridge refused until this operator confirms them, on /voice.

  The voice loop's turns have no chat, so this is where its refused arm/hand
  calls (T2, high-danger T1) wait. ONE ACTION: «Confirmar» makes back grant and
  run that exact call (the arguments shown), and the loop's next turn hears the
  outcome; «Cancelar» drops it. Same endpoint as the chat card (`decideCall`).

  BUTTONS ONLY, NO SPOKEN «confirmo» HERE: this conversation hears through the
  robot's own microphone, so anyone near it (or its own voice) could say it.
  Spoken confirmation exists only on the headset's push-to-talk.
-->
<script lang="ts">
  import { PUBLIC_API_URL } from "$app/env/public";
  import { decideCall, type Decision } from "#lib/chat/confirm.js";
  import { getVoiceLoop } from "#lib/robot/context.js";
  import { OUTCOME_TONE, type SkillOutcome } from "#lib/robot/skill-outcome.js";
  import { pendingConfirmations } from "./voice-view";

  const voice = getVoiceLoop();
  const rows = $derived(pendingConfirmations(voice.state));
  let busy = $state<string | null>(null);
  let last = $state<{ tool: string; outcome: SkillOutcome } | null>(null);

  async function decide(
    row: (typeof rows)[number],
    decision: Decision,
  ): Promise<void> {
    if (busy) return;
    busy = row.id;
    try {
      const r = await decideCall(
        PUBLIC_API_URL,
        row.tool,
        row.id,
        row.args,
        decision,
      );
      last = { tool: row.tool, outcome: r.outcome };
    } finally {
      busy = null;
    }
  }
</script>

{#if rows.length || last}
  <section
    class="flex min-w-0 flex-col gap-3 panel p-5"
    aria-labelledby="pending-confirmations-heading"
  >
    <h2 id="pending-confirmations-heading" class="eyebrow">
      Esperando tu confirmación
    </h2>
    {#each rows as row (row.id)}
      <div class="flex min-w-0 flex-col gap-2 tile p-3">
        <p class="text-sm text-ink">
          El agente quiere ejecutar <strong>{row.tool}</strong> con:
        </p>
        <code class="max-w-full min-w-0 readout text-xs break-all text-ink-dim"
          >{row.argsText}</code
        >
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy !== null}
            onclick={() => decide(row, "confirm")}
            class="inline-flex h-9 items-center justify-center rounded-lg cta px-3 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
          >
            {busy === row.id
              ? `Ejecutando ${row.tool}…`
              : `Confirmar ${row.tool}`}
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onclick={() => decide(row, "cancel")}
            class="inline-flex h-9 items-center justify-center rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
          >
            Cancelar
          </button>
        </div>
      </div>
    {/each}
    {#if last}
      <p class={["text-sm", OUTCOME_TONE[last.outcome.kind]]} role="status">
        {last.tool}: {last.outcome.text}{#if last.outcome.code}
          · <span class="readout">{last.outcome.code}</span>{/if}
        {#if last.outcome.detail}— {last.outcome.detail}{/if}
      </p>
    {/if}
    <p class="text-xs text-ink-mute">
      Solo con los botones: esta conversación escucha por el micrófono del
      robot, así que un «confirmo» dicho cerca de él no cuenta.
    </p>
  </section>
{/if}
