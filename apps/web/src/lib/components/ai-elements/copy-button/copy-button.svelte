<script lang="ts">
  import { Button } from "$lib/components/ui/button/index.js";
  import { UseClipboard } from "$lib/hooks/use-clipboard.svelte";
  import { cn } from "$lib/utils";
  import CheckIcon from "@lucide/svelte/icons/check";
  import CopyIcon from "@lucide/svelte/icons/copy";
  import XIcon from "@lucide/svelte/icons/x";
  import { scale } from "svelte/transition";
  import type { CopyButtonProps } from "./types.ts";

  // The registry defaulted `tabindex` to -1, which took every copy button in
  // the chat (tool parameters, tool results, replies) out of the tab order —
  // a keyboard operator could see them and never reach them. Left to the
  // caller now; the default is the button's natural focusability.
  let {
    ref = $bindable(null),
    text,
    icon,
    animationDuration = 500,
    variant = "ghost",
    size = "icon",
    onCopy,
    class: className,
    tabindex,
    label = "Copiar",
    children,
  }: CopyButtonProps = $props();

  // this way if the user passes text then the button will be the default size
  // svelte-ignore state_referenced_locally
  if (size === "icon" && children) {
    size = "default";
  }

  let clipboard = new UseClipboard();

  // The name stays put ("Copiar …") so the button is findable; the outcome is
  // announced separately through a polite live region, because swapping the
  // button's own text is not reliably read out.
  const outcome = $derived(
    clipboard.status === "success"
      ? "Copiado"
      : clipboard.status === "failure"
        ? "No se pudo copiar"
        : "",
  );
</script>

<Button
  bind:ref
  {variant}
  {size}
  {tabindex}
  class={cn("flex items-center gap-2", className)}
  type="button"
  name="copy"
  title={outcome || label}
  onclick={async () => {
    const status = await clipboard.copy(text);

    onCopy?.(status);
  }}
>
  {#if clipboard.status === "success"}
    <div in:scale={{ duration: animationDuration, start: 0.85 }}>
      <CheckIcon aria-hidden="true" strokeWidth={1.8} />
    </div>
  {:else if clipboard.status === "failure"}
    <div in:scale={{ duration: animationDuration, start: 0.85 }}>
      <XIcon aria-hidden="true" strokeWidth={1.8} />
    </div>
  {:else}
    <div in:scale={{ duration: animationDuration, start: 0.85 }}>
      {#if icon}
        {@render icon()}
      {:else}
        <CopyIcon aria-hidden="true" strokeWidth={1.8} />
      {/if}
    </div>
  {/if}
  <span class="sr-only">{label}</span>
  <span class="sr-only" aria-live="polite">{outcome}</span>
  {@render children?.()}
</Button>
