<!--
  Collapsed background: the runbook behind a panel.

  The page carried a lot of developer prose inline — script paths, env vars,
  module names — between the operator and the controls, in a headset. It is all
  still true and still useful to whoever is debugging, so it is kept, folded
  away. A native <details>: keyboard- and hand-ray-operable with no script, and
  the summary row is a full 48 px target.
-->
<script lang="ts">
  import type { Snippet } from "svelte";
  import { ChevronRight } from "@lucide/svelte";
  import { cn } from "$lib/utils";

  let {
    summary = "Detalles",
    children,
    class: className,
    summaryClass,
    open = false,
  }: {
    summary?: string;
    children: Snippet;
    class?: string;
    /** For a summary that is a feature ("Más gestos"), not a footnote. */
    summaryClass?: string;
    open?: boolean;
  } = $props();
</script>

<details class={cn("group", className)} {open}>
  <summary
    class={cn(
      "flex min-h-12 cursor-pointer list-none items-center gap-1.5 rounded-md text-xs font-medium text-ink-mute select-none hover:text-ink-dim focus-visible:ring-3 focus-visible:ring-cyan-hot/50 focus-visible:outline-none [&::-webkit-details-marker]:hidden",
      summaryClass,
    )}
  >
    <ChevronRight
      class="size-3.5 transition-transform group-open:rotate-90 motion-reduce:transition-none"
      aria-hidden="true"
    />
    {summary}
  </summary>
  <div class="flex flex-col gap-2 pb-1 text-sm leading-relaxed text-ink-mute">
    {@render children()}
  </div>
</details>
