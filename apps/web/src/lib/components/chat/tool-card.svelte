<script lang="ts">
  /*
   * One tool call in an agent transcript.
   *
   * What the card says comes from `classifyToolPart` — see `$lib/chat/tool-part.ts`
   * for the two lies it exists to stop telling (a forever-"Ejecutando" on an
   * interrupted call, and a green "Completado" on a walk that never arrived).
   *
   * Expanded while the call is driving the robot, or when it failed, so the
   * operator sees which skill is moving it right now and why one didn't;
   * collapsed otherwise, to keep long transcripts scannable. An interrupted call
   * stays collapsed, but its "may have run" line sits under the header where it
   * can't be missed.
   */
  import * as Tool from "$lib/components/ai-elements/tool/index.js";
  import {
    classifyToolPart,
    toolName,
    type ToolPartLike,
  } from "$lib/chat/tool-part";

  let {
    part,
    live,
  }: {
    part: ToolPartLike;
    /** Part of the last message while the chat is busy. */
    live: boolean;
  } = $props();

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
