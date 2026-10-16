<script lang="ts">
  import { cn } from "#lib/utils.js";
  import * as Code from "#lib/components/ai-elements/code/index.js";
  import type { Snippet } from "svelte";
  import type { SupportedLanguage } from "../code/shiki";

  interface ToolOutputProps {
    class?: string;
    output?: any;
    /** Failure sentence, Spanish. */
    errorText?: string | null;
    /** The bridge's own words for the failure (often English), verbatim. */
    errorDetail?: string | null;
    children?: Snippet;
    [key: string]: any;
  }

  let {
    class: className = "",
    output,
    errorText,
    errorDetail,
    children,
    ...restProps
  }: ToolOutputProps = $props();

  type OutputComp = {
    type: "code" | "text";
    content: string;
    language: SupportedLanguage;
  };

  let outputComponent: OutputComp | null = $derived.by(() => {
    if (output === undefined || output === null || output === "") return null;

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

<!-- The error and the result are both shown when both exist: a firmware
     refusal's Spanish hint says what went wrong, and the raw body beside it is
     what someone debugging the bridge needs. The registry showed one or the
     other. -->
{#if errorText || outputComponent || children}
  <div {id} class={cn("space-y-3 px-3 pb-3", className)} {...restProps}>
    {#if errorText}
      <div class="space-y-2">
        <p class="eyebrow">Error</p>
        <div
          class="rounded-md border border-danger/30 bg-danger/10 p-3 text-xs text-danger-soft"
        >
          <p>{errorText}</p>
          {#if errorDetail}
            <p
              class="mt-1 readout break-words whitespace-pre-wrap text-danger-soft"
            >
              <span lang="en">{errorDetail}</span>
            </p>
          {/if}
        </div>
      </div>
    {/if}
    {#if outputComponent}
      <div class="space-y-2">
        <p class="eyebrow">Resultado</p>
        <div
          class="overflow-hidden rounded-md border border-hairline bg-trench/60 text-xs text-foreground"
        >
          {#if outputComponent.type === "code"}
            <Code.Root
              code={outputComponent.content}
              lang={outputComponent.language}
              hideLines
            >
              <Code.CopyButton label="Copiar resultado" />
            </Code.Root>
          {:else}
            <div class="p-3">{outputComponent.content}</div>
          {/if}
        </div>
      </div>
    {/if}
    {@render children?.()}
  </div>
{/if}
