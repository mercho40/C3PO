<script lang="ts">
  // Markdown renderer for assistant text and reasoning.
  //
  // The registry ships this on `streamdown-svelte`, which we deliberately do
  // not use: Streamdown hard-depends on the full Shiki bundle (its `markdown`
  // grammar alone statically embeds ~50 more), plus mermaid and katex. That
  // pushed the client build past a 2 GB heap and put ~15 MB of TextMate
  // grammars in front of an operator who is often on the robot's own Wi-Fi —
  // to syntax-highlight code the agent never emits. Its replies are short
  // status prose; tool arguments and results are JSON, and those already get
  // real highlighting from the sibling `code` element.
  //
  // So: svelte-exmarkdown + GFM (the approach vercel/ai-chatbot-svelte uses),
  // with the console's prose styling. The `content` prop is kept because the
  // other ai-elements (reasoning, message) render through this component.
  import Markdown from "svelte-exmarkdown";
  import { gfmPlugin } from "svelte-exmarkdown/gfm";
  import { cn } from "$lib/utils";

  let { content = "", class: className }: { content?: string; class?: string } =
    $props();
</script>

<div
  class={cn(
    `min-w-0 text-sm leading-relaxed text-ink-dim
		[&_a]:text-cyan [&_a]:underline-offset-2 [&_a:hover]:underline
		[&_blockquote]:border-l-2 [&_blockquote]:border-hairline-strong [&_blockquote]:pl-3 [&_blockquote]:text-ink-mute
		[&_code]:rounded [&_code]:bg-accent [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_code]:text-peri
		[&_em]:italic
		[&_h1]:mt-3 [&_h1]:mb-1.5 [&_h1]:text-base [&_h1]:font-semibold [&_h1]:text-ink
		[&_h2]:mt-3 [&_h2]:mb-1.5 [&_h2]:text-[15px] [&_h2]:font-semibold [&_h2]:text-ink
		[&_h3]:mt-2.5 [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-ink
		[&_hr]:my-3 [&_hr]:border-hairline
		[&_li]:my-0.5 [&_ol]:my-1.5 [&_ol]:ml-4 [&_ol]:list-decimal [&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0
		[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-lg
		[&_pre]:border [&_pre]:border-hairline [&_pre]:bg-trench [&_pre]:p-3 [&_pre_code]:bg-transparent
		[&_pre_code]:p-0 [&_pre_code]:text-ink-dim [&_strong]:font-semibold [&_strong]:text-ink [&_table]:my-2
		[&_table]:w-full [&_table]:text-left
		[&_td]:border-b [&_td]:border-hairline [&_td]:py-1
		[&_td]:pr-3 [&_th]:border-b [&_th]:border-hairline-strong [&_th]:py-1
		[&_th]:pr-3 [&_th]:font-semibold [&_ul]:my-1.5 [&_ul]:ml-4 [&_ul]:list-disc`,
    className,
  )}
>
  <Markdown md={content} plugins={[gfmPlugin()]} />
</div>
