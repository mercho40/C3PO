<script lang="ts" module>
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLAttributes } from "svelte/elements";
  import type { Snippet } from "svelte";

  export interface ConversationContentProps extends WithElementRef<
    HTMLAttributes<HTMLDivElement>
  > {
    children?: Snippet;
  }
</script>

<script lang="ts">
  import { getStickToBottomContext } from "./stick-to-bottom-context.svelte.js";
  import { watch } from "runed";

  let {
    class: className,
    children,
    ref = $bindable(null),
    ...restProps
  }: ConversationContentProps = $props();

  const context = getStickToBottomContext();

  // The registry version bound this element twice (`bind:this={element}` and
  // `bind:this={ref}` on the same node), so the two references fought over the
  // node and `ref` was the only one callers could rely on. One binding, and the
  // scroll context reads from it.
  watch(
    () => ref,
    () => {
      if (ref) {
        context.setElement(ref);
        // Initial scroll to bottom
        context.scrollToBottom("smooth");
      }
    },
  );
</script>

<div
  bind:this={ref}
  class={cn("flex flex-col gap-8 p-4", className)}
  {...restProps}
>
  {@render children?.()}
</div>
