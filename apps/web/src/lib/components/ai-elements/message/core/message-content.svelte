<script lang="ts">
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  interface Props extends HTMLAttributes<HTMLDivElement> {
    class?: string;
    children: Snippet;
  }

  let { class: className, children, ...restProps }: Props = $props();

  // Assistant turns take the full column. With the registry's `w-fit` a tool
  // card's `w-full` resolved to the width of its own header, so a turn holding
  // only a tool call got a narrow card that jumped wider the moment it was
  // expanded, and every card in a transcript ended up a different width.
</script>

<div
  class={cn(
    "flex w-fit max-w-full min-w-0 flex-col gap-2 overflow-hidden text-sm",
    "group-[.is-user]:ml-auto group-[.is-user]:rounded-lg group-[.is-user]:bg-secondary group-[.is-user]:px-4 group-[.is-user]:py-3 group-[.is-user]:text-foreground",
    "group-[.is-assistant]:w-full group-[.is-assistant]:text-foreground",
    className,
  )}
  {...restProps}
>
  {@render children()}
</div>
