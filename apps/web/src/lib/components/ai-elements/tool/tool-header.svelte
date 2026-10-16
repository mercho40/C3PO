<script lang="ts">
  import { CollapsibleTrigger } from "#lib/components/ui/collapsible/index.js";
  import { cn } from "#lib/utils.js";

  import CheckCircleIcon from "@lucide/svelte/icons/check-circle";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import CircleDashedIcon from "@lucide/svelte/icons/circle-dashed";
  import ClockIcon from "@lucide/svelte/icons/clock";
  import TriangleAlertIcon from "@lucide/svelte/icons/triangle-alert";
  import WrenchIcon from "@lucide/svelte/icons/wrench";
  import XCircleIcon from "@lucide/svelte/icons/x-circle";
  import type { ToolTone } from "#lib/chat/tool-part.js";

  /*
   * The header no longer maps the AI SDK's part state to a label itself. That
   * mapping said "Completado" for anything that came back and "Ejecutando" for
   * anything that hadn't — including a walk that timed out, a stub that only
   * logged, and a call interrupted an hour ago. The caller now decides what the
   * call MEANT (`#lib/chat/tool-part.ts`, on top of the shared skill-outcome
   * classifier) and hands down the words and the tone.
   *
   * The registry's `durationMs` prop is gone: nothing ever passed it.
   */
  interface ToolHeaderProps {
    /** Skill name, shown verbatim in mono. */
    name: string;
    /** Badge text, Spanish. */
    label: string;
    tone: ToolTone;
    /** The bridge says it only pretended (stub / logged_only). */
    simulated?: boolean;
    class?: string;
    [key: string]: any;
  }

  let {
    name,
    label,
    tone,
    simulated = false,
    class: className = "",
    ...restProps
  }: ToolHeaderProps = $props();

  const ICONS = {
    running: ClockIcon,
    ok: CheckCircleIcon,
    warn: TriangleAlertIcon,
    error: XCircleIcon,
    neutral: CircleDashedIcon,
  } as const satisfies Record<ToolTone, unknown>;

  // Colour carries the outcome; the operator scans a long transcript for the
  // red and the amber ones, so status must not be a uniform grey badge. Running
  // is deliberately NOT cyan: cyan means "moving under power", and most skills
  // (get_state, say, listen) move nothing.
  const TONES = {
    running: "border-hairline-strong bg-wash text-ink-dim",
    ok: "border-ok/30 bg-ok/10 text-ok",
    warn: "border-warn/30 bg-warn/10 text-warn",
    error: "border-danger/30 bg-danger/10 text-danger-soft",
    neutral: "border-hairline-strong bg-wash text-ink-mute",
  } as const satisfies Record<ToolTone, string>;

  const StatusIcon = $derived(ICONS[tone]);

  let id = $props.id();
</script>

<CollapsibleTrigger
  {id}
  class={cn(
    "flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors outline-none hover:bg-wash-hover focus-visible:bg-wash-hover focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:ring-inset",
    className,
  )}
  {...restProps}
>
  <WrenchIcon class="size-3.5 shrink-0 text-ink-mute" aria-hidden="true" />
  <span class="truncate font-mono text-xs text-ink">{name}</span>
  {#if simulated}
    <span
      class="shrink-0 rounded border border-hairline-strong px-1.5 text-2xs text-ink-mute"
      title="El puente informa que no se envió nada al robot"
    >
      simulación
    </span>
  {/if}
  <span
    class={cn(
      "ml-auto inline-flex max-w-[60%] min-w-0 shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs tracking-wide uppercase",
      TONES[tone],
    )}
    title={label}
  >
    <StatusIcon
      class={cn("size-3 shrink-0", tone === "running" && "animate-pulse")}
      aria-hidden="true"
    />
    <span class="truncate">{label}</span>
  </span>
  <ChevronDownIcon
    class="size-3.5 shrink-0 text-ink-mute transition-transform group-data-[state=open]:rotate-180"
    aria-hidden="true"
  />
</CollapsibleTrigger>
