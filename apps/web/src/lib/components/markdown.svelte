<script lang="ts">
  // Markdown renderer for chat messages. The svelte-exmarkdown + GFM approach is
  // adapted from vercel/ai-chatbot-svelte (Apache-2.0); the styling here is the
  // C3PO console theme, applied via descendant utilities rather than per-tag
  // snippet overrides.
  import Markdown from "svelte-exmarkdown";
  import { gfmPlugin } from "svelte-exmarkdown/gfm";

  let { md }: { md: string } = $props();
</script>

<div
  class="text-[13px] leading-relaxed text-[#c6dcff]
    [&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0
    [&_a]:text-[#9ae5f8] [&_a]:underline-offset-2 [&_a:hover]:underline
    [&_ul]:my-1.5 [&_ul]:ml-4 [&_ul]:list-disc
    [&_ol]:my-1.5 [&_ol]:ml-4 [&_ol]:list-decimal
    [&_li]:my-0.5
    [&_strong]:font-semibold [&_strong]:text-[#eaf1ff]
    [&_em]:italic
    [&_h1]:mt-3 [&_h1]:mb-1.5 [&_h1]:text-base [&_h1]:font-semibold [&_h1]:text-[#eaf1ff]
    [&_h2]:mt-3 [&_h2]:mb-1.5 [&_h2]:text-[15px] [&_h2]:font-semibold [&_h2]:text-[#eaf1ff]
    [&_h3]:mt-2.5 [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-[#eaf1ff]
    [&_code]:rounded [&_code]:bg-[rgba(180,210,255,0.1)] [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[12px] [&_code]:text-[#9fc5ff]
    [&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-[rgba(180,210,255,0.08)] [&_pre]:bg-[#06090f] [&_pre]:p-3
    [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-[#c6dcff]
    [&_blockquote]:border-l-2 [&_blockquote]:border-[rgba(180,210,255,0.2)] [&_blockquote]:pl-3 [&_blockquote]:text-[#8a96ad]
    [&_hr]:my-3 [&_hr]:border-[rgba(180,210,255,0.08)]
    [&_table]:my-2 [&_table]:w-full [&_table]:text-left
    [&_th]:border-b [&_th]:border-[rgba(180,210,255,0.12)] [&_th]:py-1 [&_th]:pr-3 [&_th]:font-semibold
    [&_td]:border-b [&_td]:border-[rgba(180,210,255,0.06)] [&_td]:py-1 [&_td]:pr-3"
>
  <Markdown {md} plugins={[gfmPlugin()]} />
</div>
