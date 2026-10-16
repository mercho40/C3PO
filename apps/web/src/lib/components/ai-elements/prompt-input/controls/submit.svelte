<script lang="ts">
  import { cn } from "#lib/utils.js";
  import { mergeProps } from "bits-ui";
  import type { ChatStatus } from "../context/types.js";
  import CircleStopIcon from "@lucide/svelte/icons/circle-stop";
  import SendIcon from "@lucide/svelte/icons/send";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";

  import {
    buttonVariants,
    type ButtonSize,
    type ButtonVariant,
  } from "#lib/components/ui/button/index.js";

  import type { HTMLButtonAttributes } from "svelte/elements";

  type SubmitClickEvent = MouseEvent & {
    currentTarget: EventTarget & HTMLButtonElement;
  };

  interface Props extends Omit<
    HTMLButtonAttributes,
    "type" | "onclick" | "aria-label"
  > {
    class?: string;
    variant?: ButtonVariant;
    size?: ButtonSize;
    status?: ChatStatus;
    onStop?: () => void;
    ref?: HTMLButtonElement | null;
    onclick?: (event: SubmitClickEvent) => void;
    /** Accessible name + tooltip while idle. */
    sendLabel?: string;
    /** Accessible name while a reply is in flight. */
    stopLabel?: string;
    /** Tooltip while a reply is in flight — say what stopping does NOT do. */
    stopHint?: string;
    children?: import("svelte").Snippet;
  }

  let {
    class: className,
    ref = $bindable(null),
    variant = "default",
    size = "icon",
    status = "ready",
    onStop,
    onclick,
    sendLabel = "Enviar",
    stopLabel = "Detener respuesta",
    stopHint,
    disabled,
    children,
    ...props
  }: Props = $props();

  let isGenerating = $derived(status === "submitted" || status === "streaming");

  // Two faces, not four. The registry showed a spinner while `submitted` (and
  // clicking the spinner stopped the turn), a square while streaming — the same
  // glyph as the robot e-stop in the header, which this button is NOT — and an
  // X after an error that, being type=submit, actually SENT the next message.
  // Now: a circled stop while anything is in flight, a send arrow otherwise.
  let Icon = $derived(isGenerating ? CircleStopIcon : SendIcon);

  let buttonType = $derived.by((): "button" | "submit" => {
    return isGenerating ? "button" : "submit";
  });

  let ariaLabel = $derived(isGenerating ? stopLabel : sendLabel);
  let tooltip = $derived(
    isGenerating ? (stopHint ?? stopLabel) : `${sendLabel} (Enter)`,
  );

  let handleClick = (event: SubmitClickEvent) => {
    if (isGenerating) {
      event.preventDefault();
      onStop?.();
      return;
    }

    onclick?.(event);
  };
</script>

<!-- Always wrapped in the tooltip, so the button element survives the
     ready → streaming flip and keeps keyboard focus. -->
<Tooltip.Root>
  <Tooltip.Trigger>
    {#snippet child({ props: triggerProps })}
      <!-- `data-prompt-input-submit` is what the textarea's Enter handler looks
           for: it refuses to submit while this button is disabled or has
           flipped to type=button (a reply in flight). Without the attribute it
           fell through to a bare `form.requestSubmit()`, the page refused the
           send, and the composer cleared the operator's draft anyway. -->
      <button
        {...mergeProps(triggerProps, props, { onclick: handleClick })}
        bind:this={ref}
        aria-label={ariaLabel}
        class={cn(
          buttonVariants({ variant, size }),
          "gap-1.5 rounded-lg",
          className,
        )}
        data-slot="button"
        data-prompt-input-submit
        data-generating={isGenerating ? "" : undefined}
        disabled={isGenerating ? false : disabled}
        type={buttonType}
      >
        {#if children}
          {@render children()}
        {:else}
          <Icon class="size-4" aria-hidden="true" />
        {/if}
      </button>
    {/snippet}
  </Tooltip.Trigger>
  <Tooltip.Content class="max-w-64 text-pretty">
    <p>{tooltip}</p>
  </Tooltip.Content>
</Tooltip.Root>
