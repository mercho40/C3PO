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
  import { createApi } from "#lib/api.js";
  import {
    classifyToolPart,
    needsConfirmation,
    toolName,
    type ToolPartLike,
  } from "#lib/chat/tool-part.js";

  let {
    part,
    live,
    onconfirmed,
  }: {
    part: ToolPartLike;
    /** Part of the last message while the chat is busy. */
    live: boolean;
    /** Called once the bridge granted the confirmation, to let the agent retry. */
    onconfirmed?: (tool: string) => void;
  } = $props();

  const confirmable = $derived(onconfirmed ? needsConfirmation(part) : null);
  let confirming = $state(false);
  let confirmed = $state(false);
  let confirmError = $state<string | null>(null);

  // One grant covers ONE call with exactly these arguments within 30 s
  // (bridge/safety.py); apps/back holds the secret and checks the admin role.
  // Shown beside the button: Tool.Input is collapsed in this state.
  const confirmArgs = $derived(JSON.stringify(part.input ?? {}));
  async function confirm(tool: string) {
    confirming = true;
    confirmError = null;
    try {
      const { error } = await createApi(fetch)
        .skills({ name: tool })
        .confirm.post({ args: (part.input ?? {}) as Record<string, unknown> });
      if (error) {
        const body = error.value as { error?: string } | undefined;
        confirmError = `No se pudo confirmar (${error.status}${body?.error ? `: ${body.error}` : ""}).`;
        return;
      }
      confirmed = true;
      onconfirmed?.(tool);
    } catch {
      confirmError = "No se pudo confirmar: sin respuesta del servidor.";
    } finally {
      confirming = false;
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
        disabled={confirming || confirmed}
        onclick={() => confirm(confirmable)}
      >
        {confirmed ? `Confirmado: ${confirmable}` : `Confirmar ${confirmable}`}
      </Button>
      <code
        class="max-w-full min-w-0 truncate readout text-xs text-ink-mute"
        title={confirmArgs}>{confirmArgs}</code
      >
      {#if confirmError}
        <p class="text-xs text-warn" role="alert">{confirmError}</p>
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
