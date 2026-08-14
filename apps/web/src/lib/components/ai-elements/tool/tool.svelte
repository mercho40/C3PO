<script lang="ts">
  import { Collapsible } from "$lib/components/ui/collapsible/index.js";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

  interface ToolProps {
    class?: string;
    children?: Snippet;
    /** Start expanded — used for the call that is currently running. */
    open?: boolean;
    [key: string]: any;
  }

  let {
    class: className = "",
    children,
    open = $bindable(false),
    ...restProps
  }: ToolProps = $props();
</script>

<!-- `group` is what the header's chevron rotation keys off
     (`group-data-[state=open]:rotate-180`); without it the disclosure arrow
     never turned. -->
<Collapsible
  bind:open
  class={cn(
    "group not-prose w-full overflow-hidden rounded-lg border",
    className,
  )}
  {...restProps}
>
  {@render children?.()}
</Collapsible>
