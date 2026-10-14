<!--
  One skill result, with its age.

  The outcome colour ternary used to be copy-pasted under every tile, in cyan for
  success — and cyan means "moving under power" in this console, not "the
  request went through". It also never said how old a result was, so "Listo"
  from a minute ago sat next to live posture reading like a current fact. The
  age is the honest half of the line.
-->
<script lang="ts">
  import { OUTCOME_TONE } from "#lib/robot/skill-outcome.js";
  import { formatSince } from "#lib/robot/format.js";
  import { outcomeDetail, type StampedOutcome } from "#lib/robot/vr-logic.js";
  import { cn } from "#lib/utils.js";

  let {
    stamped,
    now,
    class: className,
    showDetail = true,
  }: {
    stamped: StampedOutcome;
    now: number;
    class?: string;
    /** The bridge's own sentence under the label (verbatim, often English). */
    showDetail?: boolean;
  } = $props();

  const o = $derived(stamped.outcome);
  const detail = $derived(showDetail ? outcomeDetail(o) : null);
</script>

<span class={cn("flex min-w-0 flex-col gap-0.5 text-xs", className)}>
  <span>
    <span class={OUTCOME_TONE[o.kind]}>{o.text}</span>
    <span class="text-ink-mute"> · {formatSince(stamped.at, now)}</span>
  </span>
  {#if detail}
    <span
      lang={detail.lang}
      class="line-clamp-3 text-2xs break-words text-ink-mute"
    >
      {detail.text}
    </span>
  {/if}
</span>
