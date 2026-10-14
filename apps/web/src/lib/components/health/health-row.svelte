<!--
  One subsystem on /health. Everything it says was decided in
  `#lib/robot/health.ts`; this only lays it out.

  Bridge text (hints, errors, notes) is shown verbatim and tagged English, so a
  screen reader pronounces "perception_up nav2" as what it is — after its
  Spanish rendering when `health.ts` knows the string.
-->
<script lang="ts">
  import {
    CircleAlert,
    CircleCheck,
    CircleDashed,
    TriangleAlert,
  } from "@lucide/svelte";
  import {
    TONE_LABEL,
    TONE_TEXT,
    type HealthRow,
    type HealthTone,
  } from "#lib/robot/health.js";

  let { row }: { row: HealthRow } = $props();

  const ICON = {
    ok: CircleCheck,
    warn: TriangleAlert,
    danger: CircleAlert,
    unknown: CircleDashed,
  } as const;

  const Icon = $derived(ICON[row.tone]);
  const headingId = $derived(`health-${row.id}`);
  const factTone = (t: HealthTone | undefined) =>
    t ? TONE_TEXT[t] : "text-ink-dim";
</script>

<article
  class="flex min-w-0 flex-col gap-3 panel p-4 sm:p-5"
  aria-labelledby={headingId}
>
  <div class="flex items-start justify-between gap-3">
    <div class="flex min-w-0 items-center gap-2">
      <Icon class="size-4 shrink-0 {TONE_TEXT[row.tone]}" aria-hidden="true" />
      <h3 id={headingId} class="text-sm font-medium text-ink">{row.name}</h3>
    </div>
    <span class="shrink-0 readout {TONE_TEXT[row.tone]}"
      >{TONE_LABEL[row.tone]}</span
    >
  </div>

  <div class="flex min-w-0 flex-col gap-1">
    <p
      class="text-sm break-words {row.tone === 'unknown'
        ? 'text-ink-dim'
        : TONE_TEXT[row.tone]}"
    >
      {row.summary}
    </p>
    {#if row.age}
      <p class="readout">{row.age}</p>
    {/if}
  </div>

  {#if row.detail}
    <p class="text-sm leading-relaxed text-ink-mute">{row.detail}</p>
  {/if}

  {#if row.bridgeText}
    <!-- A known bridge string reads in Spanish first; the bridge's own words
         follow, verbatim and tagged English, for whoever greps its logs.
         Unknown text is shown alone — never guessed at. -->
    <div class="flex min-w-0 flex-col gap-0.5">
      {#if row.bridgeEs}
        <p class="text-sm leading-relaxed break-words text-ink-dim">
          {row.bridgeEs}
        </p>
      {/if}
      <p
        class="readout break-words {row.bridgeEs
          ? 'text-ink-mute'
          : 'text-ink-dim'}"
      >
        <span class="sr-only">Texto del puente: </span><span lang="en"
          >{row.bridgeText}</span
        >
      </p>
    </div>
  {/if}

  {#if row.action}
    <p class="text-sm text-ink-dim">
      <span class="font-medium text-ink">Qué hacer:</span>
      {row.action}
    </p>
  {/if}

  {#if row.faults?.length}
    <ul class="flex flex-col gap-2">
      {#each row.faults as fault (fault.raw)}
        <li class="flex flex-col gap-0.5 tile px-3 py-2">
          <span
            class="text-sm {fault.severity === 'danger'
              ? 'text-danger-soft'
              : 'text-warn'}"
          >
            {fault.text}
          </span>
          {#if fault.action}
            <span class="text-xs text-ink-dim">{fault.action}</span>
          {/if}
          <span class="readout break-all">
            {fault.scope === "robot" ? "robot" : "puente"} ·
            <span lang="en">{fault.raw}</span>
          </span>
        </li>
      {/each}
    </ul>
  {/if}

  {#if row.facts.length}
    <dl
      class="grid grid-cols-1 gap-x-4 gap-y-2 border-t border-hairline pt-3 min-[420px]:grid-cols-2"
    >
      {#each row.facts as fact (fact.label)}
        <div class="flex min-w-0 flex-col gap-0.5">
          <dt class="eyebrow">{fact.label}</dt>
          <dd class="font-mono text-xs break-words {factTone(fact.tone)}">
            {#if fact.lang}<span lang={fact.lang}>{fact.value}</span
              >{:else}{fact.value}{/if}
          </dd>
        </div>
      {/each}
    </dl>
  {/if}
</article>
