<script lang="ts">
  import { cn } from "$lib/utils";
  import { watch } from "runed";
  import { Collapsible } from "$lib/components/ui/collapsible/index.js";
  import {
    ReasoningContext,
    setReasoningContext,
  } from "./reasoning-context.svelte";
  import { untrack } from "svelte";

  interface Props {
    class?: string;
    /**
     * True only while THIS block is the one the model is writing. The chat used
     * to pass the chat-wide status here, so every saved block in the transcript
     * flipped back to "Razonando…" on each new turn and then all inherited the
     * new turn's duration when it ended.
     */
    isStreaming?: boolean;
    open?: boolean;
    /** Initial disclosure. Defaults to open while streaming, closed otherwise. */
    defaultOpen?: boolean;
    onOpenChange?: (open: boolean) => void;
    duration?: number;
    children?: import("svelte").Snippet;
  }

  let {
    class: className = "",
    isStreaming = false,
    open = $bindable(),
    defaultOpen,
    onOpenChange,
    duration = $bindable(),
    children,
    ...props
  }: Props = $props();

  const AUTO_CLOSE_DELAY = 1000;
  const MS_IN_S = 1000;

  // A block reloaded from the database starts closed — it is history, and the
  // registry's open-then-snap-shut on every page load made a transcript of ten
  // turns flicker ten times. One the model is writing right now starts open.
  const initialOpen = untrack(() => open ?? defaultOpen ?? isStreaming);

  let reasoningContext = new ReasoningContext({
    isStreaming: untrack(() => isStreaming),
    isOpen: initialOpen,
    duration: untrack(() => duration ?? 0),
  });

  let isOpen = $state(initialOpen);
  // Not reactive: nothing renders it, it only times the live stretch.
  let startTime: number | null = null;

  // Sync external props to context and local state
  $effect(() => {
    reasoningContext.isStreaming = isStreaming;
  });

  $effect(() => {
    if (open !== undefined) {
      isOpen = open;
      reasoningContext.isOpen = open;
    }
  });

  $effect(() => {
    if (duration !== undefined) {
      reasoningContext.duration = duration;
    }
  });

  // Time the block only across a live → done transition we actually watched.
  // A saved block never streamed here, so it keeps an unknown duration (the
  // trigger then says "Razonamiento" rather than inventing a number).
  watch(
    () => isStreaming,
    (streaming) => {
      if (streaming) {
        startTime ??= Date.now();
        if (!isOpen) handleOpenChange(true);
        return;
      }
      if (startTime === null) return;

      const measured = Math.max(
        1,
        Math.ceil((Date.now() - startTime) / MS_IN_S),
      );
      reasoningContext.duration = measured;
      if (duration !== undefined) duration = measured;
      startTime = null;

      // Fold it away a beat after the model moves on, so the operator sees it
      // finish before it collapses.
      const timer = setTimeout(() => handleOpenChange(false), AUTO_CLOSE_DELAY);
      return () => clearTimeout(timer);
    },
  );

  function handleOpenChange(newOpen: boolean) {
    isOpen = newOpen;
    reasoningContext.setIsOpen(newOpen);

    if (open !== undefined) {
      open = newOpen;
    }

    onOpenChange?.(newOpen);
  }

  // Set the context for child components
  setReasoningContext(reasoningContext);
</script>

<Collapsible
  class={cn("not-prose mb-4", className)}
  bind:open={isOpen}
  onOpenChange={handleOpenChange}
  {...props}
>
  {@render children?.()}
</Collapsible>
