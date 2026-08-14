<script lang="ts">
  import { cn } from "$lib/utils";
  import * as Code from "$lib/components/ai-elements/code/index.js";

  interface ToolInputProps {
    class?: string;
    input: any;
    [key: string]: any;
  }

  let { class: className = "", input, ...restProps }: ToolInputProps = $props();

  // `JSON.stringify(undefined)` is `undefined`, not a string — and a tool call
  // whose arguments are still streaming has exactly that. Render an empty
  // object rather than handing a non-string down to the highlighter.
  let formattedInput = $derived(JSON.stringify(input ?? {}, null, 2));

  let id = $props.id();
</script>

<div
  {id}
  class={cn("space-y-2 overflow-hidden px-3 pb-3", className)}
  {...restProps}
>
  <h4 class="eyebrow">Parámetros</h4>
  <div class="overflow-hidden rounded-md border border-hairline bg-trench/60">
    <Code.Root code={formattedInput} lang="json" hideLines>
      <Code.CopyButton />
    </Code.Root>
  </div>
</div>
