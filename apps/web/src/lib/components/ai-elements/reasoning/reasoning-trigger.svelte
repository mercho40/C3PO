<script lang="ts">
  import { cn } from "#lib/utils.js";
  import { CollapsibleTrigger } from "#lib/components/ui/collapsible/index.js";
  import { getReasoningContext } from "./reasoning-context.svelte.js";
  import BrainIcon from "@lucide/svelte/icons/brain";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";

  interface Props {
    class?: string;
    children?: import("svelte").Snippet;
  }

  let { class: className = "", children, ...props }: Props = $props();

  let reasoningContext = getReasoningContext();

  // Only a block that is being written right now says "Razonando…". The
  // registry also said it whenever the duration was 0 — which is every block
  // reloaded from the database, since the duration is measured live and never
  // stored — so saved reasoning read as in progress forever.
  let thinkingMessage = $derived.by(() => {
    const { isStreaming, duration } = reasoningContext;
    if (isStreaming) return "Razonando…";
    if (duration > 0) return `Razonó ${duration} s`;
    return "Razonamiento";
  });
</script>

<CollapsibleTrigger
  class={cn(
    "flex w-full items-center gap-2 rounded-md text-sm text-ink-mute transition-colors outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50",
    className,
  )}
  {...props}
>
  {#if children}
    {@render children()}
  {:else}
    <BrainIcon
      class={cn("size-4", reasoningContext.isStreaming && "animate-pulse")}
      aria-hidden="true"
    />
    <p>{thinkingMessage}</p>
    <ChevronDownIcon
      class={cn(
        "size-4 transition-transform",
        reasoningContext.isOpen ? "rotate-180" : "rotate-0",
      )}
      aria-hidden="true"
    />
  {/if}
</CollapsibleTrigger>
