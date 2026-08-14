<script lang="ts">
  import { CollapsibleTrigger } from "$lib/components/ui/collapsible/index.js";
  import { cn } from "$lib/utils";

  import BanIcon from "@lucide/svelte/icons/ban";
  import CheckCircleIcon from "@lucide/svelte/icons/check-circle";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import CircleIcon from "@lucide/svelte/icons/circle";
  import ClockIcon from "@lucide/svelte/icons/clock";
  import ShieldAlertIcon from "@lucide/svelte/icons/shield-alert";
  import WrenchIcon from "@lucide/svelte/icons/wrench";
  import XCircleIcon from "@lucide/svelte/icons/x-circle";
  import type { ToolUIPartState } from "./tool-context.svelte.js";

  interface ToolHeaderProps {
    type: string;
    state: ToolUIPartState;
    /** Wall-clock duration, shown once the call has finished. */
    durationMs?: number | null;
    class?: string;
    [key: string]: any;
  }

  let {
    type,
    state,
    durationMs = null,
    class: className = "",
    ...restProps
  }: ToolHeaderProps = $props();

  // Spanish, to match the rest of the console. The approval states come from
  // the AI SDK's human-in-the-loop gate: no skill is registered with
  // `needsApproval` yet, but a "confirm before the robot moves" step is
  // exactly what this console will want, so they are handled rather than
  // falling through to a blank badge.
  const LABELS = {
    "input-streaming": "En cola",
    "input-available": "Ejecutando",
    "approval-requested": "Requiere aprobación",
    "approval-responded": "Aprobación enviada",
    "output-available": "Completado",
    "output-error": "Error",
    "output-denied": "Rechazado",
  } as const satisfies Record<ToolUIPartState, string>;

  const ICONS = {
    "input-streaming": CircleIcon,
    "input-available": ClockIcon,
    "approval-requested": ShieldAlertIcon,
    "approval-responded": ClockIcon,
    "output-available": CheckCircleIcon,
    "output-error": XCircleIcon,
    "output-denied": BanIcon,
  } as const satisfies Record<ToolUIPartState, unknown>;

  const StatusIcon = $derived(ICONS[state]);
  const label = $derived(LABELS[state]);

  // Colour carries the outcome; the operator scans a long transcript for the
  // red one, so status must not be a uniform grey badge.
  const tone = $derived(
    state === "output-error" || state === "output-denied"
      ? "border-danger/30 bg-danger/10 text-danger-soft"
      : state === "approval-requested"
        ? "border-warn/30 bg-warn/10 text-warn"
        : state === "output-available"
          ? "border-ok/30 bg-ok/10 text-ok"
          : "border-hairline-strong bg-wash text-ink-mute",
  );

  let id = $props.id();
</script>

<CollapsibleTrigger
  {id}
  class={cn(
    "flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-wash-hover",
    className,
  )}
  {...restProps}
>
  <WrenchIcon class="size-3.5 shrink-0 text-ink-mute" />
  <span class="truncate font-mono text-xs text-ink">{type}</span>
  <span
    class={cn(
      "ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs tracking-wide uppercase",
      tone,
    )}
  >
    <StatusIcon
      class={cn("size-3", state === "input-available" && "animate-pulse")}
    />
    {label}
  </span>
  {#if durationMs != null}
    <span class="shrink-0 readout">{(durationMs / 1000).toFixed(1)}s</span>
  {/if}
  <ChevronDownIcon
    class="size-3.5 shrink-0 text-ink-mute transition-transform group-data-[state=open]:rotate-180"
  />
</CollapsibleTrigger>
