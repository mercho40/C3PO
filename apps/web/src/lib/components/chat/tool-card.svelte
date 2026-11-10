<script lang="ts">
  /*
   * One tool call in an agent transcript.
   *
   * What the card says comes from `classifyToolPart` — see `#lib/chat/tool-part.ts`
   * for the two lies it exists to stop telling (a forever-"Ejecutando" on an
   * interrupted call, and a green "Completado" on a walk that never arrived).
   *
   * Expanded while the call is driving the robot, or when it failed, so the
   * operator sees which skill is moving it right now and why one didn't;
   * collapsed otherwise, to keep long transcripts scannable. An interrupted call
   * stays collapsed, but its "may have run" line sits under the header where it
   * can't be missed.
   */
  import * as Tool from "#lib/components/ai-elements/tool/index.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { PUBLIC_API_URL } from "$app/env/public";
  import { argsText, decideCall } from "#lib/chat/confirm.js";
  import {
    classifyToolPart,
    needsConfirmation,
    toolName,
    type ToolPartLike,
  } from "#lib/chat/tool-part.js";
  import { OUTCOME_TONE, type SkillOutcome } from "#lib/robot/skill-outcome.js";

  let {
    part,
    live,
    onconfirmed,
  }: {
    part: ToolPartLike & { toolCallId?: string };
    /** Part of the last message while the chat is busy. */
    live: boolean;
    /** Called once the confirmed call RAN, with the words the agent must read next. */
    onconfirmed?: (message: string) => void;
  } = $props();

  const confirmable = $derived(onconfirmed ? needsConfirmation(part) : null);
  let running = $state(false);
  let outcome = $state<SkillOutcome | null>(null);
  // False while back still holds the call (nothing ran): the button stays usable.
  let final = $state(false);

  // One press is the whole action: back grants this exact call (these
  // arguments, shown beside the button: Tool.Input is collapsed here) and runs
  // it, once. The agent then reads the result; it does not retry.
  const confirmArgs = $derived(argsText(part.input));
  async function confirm(tool: string) {
    running = true;
    try {
      const r = await decideCall(
        PUBLIC_API_URL,
        tool,
        part.toolCallId ?? "",
        part.input,
        "confirm",
      );
      outcome = r.outcome;
      final = r.final;
      if (r.message) onconfirmed?.(r.message);
    } finally {
      running = false;
    }
  }

  const display = $derived(classifyToolPart(part, { live }));
  const name = $derived(toolName(part));
  // Show the bridge's own words in the body only when they are not already
  // the error block's detail.
  const bodyDetail = $derived(display.errorText ? null : display.detail);
</script>

<Tool.Root class="border-hairline bg-wash" open={display.defaultOpen}>
  <Tool.Header
    {name}
    label={display.label}
    tone={display.tone}
    simulated={display.simulated}
  />
  {#if display.note}
    <p
      class={[
        "-mt-1 px-3 pb-2.5 text-xs",
        display.tone === "warn" ? "text-warn" : "text-ink-mute",
      ]}
    >
      {display.note}
    </p>
  {/if}
  {#if confirmable}
    <div class="flex flex-wrap items-center gap-2 px-3 pb-3">
      <Button
        size="sm"
        disabled={running || final || live}
        onclick={() => confirm(confirmable)}
        title={live ? "Esperá a que el agente termine su turno" : undefined}
      >
        {running
          ? `Ejecutando ${confirmable}…`
          : final
            ? `Confirmado: ${confirmable}`
            : outcome
              ? `Reintentar ${confirmable}`
              : `Confirmar ${confirmable}`}
      </Button>
      <code
        class="max-w-full min-w-0 truncate readout text-xs text-ink-mute"
        title={confirmArgs}>{confirmArgs}</code
      >
      {#if outcome}
        <p class={["w-full text-xs", OUTCOME_TONE[outcome.kind]]} role="status">
          {outcome.text}{#if outcome.code}
            · <span class="readout">{outcome.code}</span>{/if}
          {#if outcome.detail}
            — {outcome.detail}
          {/if}
        </p>
      {/if}
    </div>
  {/if}
  <Tool.Content>
    {#if bodyDetail}
      <p class="px-3 pb-3 readout break-words whitespace-pre-wrap">
        <span lang="en">{bodyDetail}</span>
      </p>
    {/if}
    <Tool.Input input={part.input} />
    <Tool.Output
      output={display.output}
      errorText={display.errorText}
      errorDetail={display.detail}
    />
  </Tool.Content>
</Tool.Root>
