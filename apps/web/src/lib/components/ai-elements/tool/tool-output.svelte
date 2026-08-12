<script lang="ts">
  import { cn } from "$lib/utils";
  import * as Code from "$lib/components/ai-elements/code/index.js";
  import type { Snippet } from "svelte";
  import type { SupportedLanguage } from "../code/shiki";

  interface ToolOutputProps {
    class?: string;
    output?: any;
    errorText?: string;
    children?: Snippet;
    [key: string]: any;
  }

  let {
    class: className = "",
    output,
    errorText,
    children,
    ...restProps
  }: ToolOutputProps = $props();

  let shouldRender = $derived.by(() => {
    return !!(output || errorText);
  });
  type OutputComp = {
    type: "code" | "text";
    content: string;
    language: SupportedLanguage;
  };

  let outputComponent: OutputComp | null = $derived.by(() => {
    if (!output) return null;

    if (typeof output === "object") {
      return {
        type: "code",
        content: JSON.stringify(output, null, 2),
        language: "json",
      };
    } else if (typeof output === "string") {
      return {
        type: "code",
        content: output,
        language: "json",
      };
    } else {
      return {
        type: "text",
        content: String(output),
        language: "text",
      };
    }
  });

  let id = $props.id();
</script>

{#if shouldRender}
  <div {id} class={cn("space-y-2 px-3 pb-3", className)} {...restProps}>
    <h4 class="eyebrow">
      {errorText ? "Error" : "Resultado"}
    </h4>
    <div
      class={cn(
        "overflow-hidden rounded-md border text-xs [&_table]:w-full",
        errorText
          ? "border-danger/30 bg-danger/10 text-danger-soft"
          : "border-hairline bg-trench/60 text-foreground",
      )}
    >
      {#if errorText}
        <div class="p-3">{errorText}</div>
      {:else if outputComponent}
        {#if outputComponent.type === "code"}
          <Code.Root
            code={outputComponent.content}
            lang={outputComponent.language}
            hideLines
          >
            <Code.CopyButton />
          </Code.Root>
        {:else}
          <div class="p-3">{outputComponent.content}</div>
        {/if}
      {/if}
    </div>
  </div>
{/if}
