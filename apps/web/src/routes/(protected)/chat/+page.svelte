<script lang="ts">
  import { tick } from "svelte";
  import { Chat } from "@ai-sdk/svelte";
  import {
    DefaultChatTransport,
    isToolOrDynamicToolUIPart,
    getToolOrDynamicToolName,
  } from "ai";
  import { PUBLIC_API_URL } from "$env/static/public";
  import { Send, Wrench, Square } from "@lucide/svelte";
  import { ScrollArea } from "$lib/components/ui/scroll-area/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Textarea } from "$lib/components/ui/textarea/index.js";
  import Markdown from "$lib/components/markdown.svelte";

  // Talks to the backend internal agent (POST /agent), which streams Claude's
  // tokens + tool calls back as a UI message stream.
  const chat = new Chat({
    transport: new DefaultChatTransport({
      api: `${PUBLIC_API_URL}/agent`,
      credentials: "include",
    }),
  });

  let input = $state("");
  let bottomEl = $state<HTMLDivElement>();

  const busy = $derived(
    chat.status === "submitted" || chat.status === "streaming",
  );
  const canSend = $derived(input.trim().length > 0 && !busy);

  const suggestions = [
    { title: "Estado del robot", label: "postura, batería y fallos", action: "¿Cuál es el estado del robot?" },
    { title: "Caminá", label: "2 metros hacia adelante", action: "Caminá 2 metros hacia adelante" },
    { title: "Pará todo", label: "detener el movimiento", action: "Pará todo movimiento" },
    { title: "Saludá", label: "hacé un gesto con la mano", action: "Saludá con la mano" },
  ];

  function send() {
    if (!canSend) return;
    chat.sendMessage({ text: input });
    input = "";
  }

  function submit(e: SubmitEvent) {
    e.preventDefault();
    send();
  }

  function onComposerKeydown(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  }

  function suggest(action: string) {
    if (busy) return;
    chat.sendMessage({ text: action });
  }

  // Follow the conversation as messages arrive / streaming starts and stops.
  $effect(() => {
    chat.messages.length;
    chat.status;
    tick().then(() => bottomEl?.scrollIntoView({ block: "end" }));
  });
</script>

<div class="flex h-full w-full flex-col gap-4">
  <ScrollArea class="min-h-0 flex-1 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828]">
    <div class="flex flex-col gap-5 p-5">
      {#if chat.messages.length === 0}
        <div class="flex flex-col items-center gap-6 py-16 text-center">
          <img
            src="/logo.svg"
            alt="C3PO"
            class="size-14 object-contain drop-shadow-[0_0_18px_rgba(126,229,255,0.5)]"
          />
          <div class="max-w-md">
            <p class="text-[15px] text-[#eaf1ff]">Hablá con el robot</p>
            <p class="mt-1 text-[13px] text-[#8a96ad]">
              Pedile estados, movimientos o gestos. El agente decide qué habilidades ejecutar.
            </p>
          </div>
          <div class="grid w-full max-w-lg gap-2 sm:grid-cols-2">
            {#each suggestions as s (s.title)}
              <button
                type="button"
                onclick={() => suggest(s.action)}
                class="flex h-auto flex-col items-start gap-0.5 rounded-xl border border-[rgba(180,210,255,0.12)] bg-[rgba(180,210,255,0.02)] px-4 py-3 text-left transition-colors hover:border-[rgba(159,197,255,0.3)] hover:bg-[rgba(159,197,255,0.06)]"
              >
                <span class="text-[13px] text-[#eaf1ff]">{s.title}</span>
                <span class="text-[12px] text-[#8a96ad]">{s.label}</span>
              </button>
            {/each}
          </div>
        </div>
      {:else}
        {#each chat.messages as message (message.id)}
          {@const isUser = message.role === "user"}
          <div class="flex gap-3 {isUser ? 'flex-row-reverse' : ''}">
            {#if !isUser}
              <img
                src="/logo.svg"
                alt="C3PO"
                class="mt-0.5 size-7 shrink-0 object-contain drop-shadow-[0_0_10px_rgba(126,229,255,0.45)]"
              />
            {/if}
            <div class="flex max-w-[80%] flex-col gap-1.5 {isUser ? 'items-end' : 'items-start'}">
              {#each message.parts as part, i (i)}
                {#if part.type === "text"}
                  {#if isUser}
                    <div class="rounded-2xl border border-[rgba(159,197,255,0.2)] bg-[rgba(159,197,255,0.14)] px-4 py-2.5 text-[13px] leading-relaxed whitespace-pre-wrap text-[#eaf1ff]">
                      {part.text}
                    </div>
                  {:else}
                    <div class="rounded-2xl border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.04)] px-4 py-2.5">
                      <Markdown md={part.text} />
                    </div>
                  {/if}
                {:else if isToolOrDynamicToolUIPart(part)}
                  <div
                    class="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[10px] {part.state ===
                    'output-error'
                      ? 'border-[rgba(255,77,106,0.3)] text-[#ff8aa0]'
                      : part.state === 'output-available'
                        ? 'border-[rgba(94,231,161,0.3)] text-[#5ee7a1]'
                        : 'border-[rgba(180,210,255,0.12)] text-[#8a96ad]'}"
                  >
                    <Wrench class="size-3" />
                    {getToolOrDynamicToolName(part)}
                    {#if part.state === "output-available"}· ✓{:else if part.state === "output-error"}· ✗{:else}· …{/if}
                  </div>
                {/if}
              {/each}
            </div>
          </div>
        {/each}
      {/if}
      <div bind:this={bottomEl}></div>
    </div>
  </ScrollArea>

  {#if chat.error}
    <div class="flex items-center justify-between gap-3 rounded-[10px] border border-[rgba(255,77,106,0.3)] bg-[rgba(255,77,106,0.06)] px-4 py-2.5 text-[12px] text-[#ff8aa0]">
      <span class="truncate">{chat.error.message}</span>
      <Button variant="outline" size="sm" class="h-7 shrink-0" onclick={() => chat.regenerate()}>Reintentar</Button>
    </div>
  {/if}

  <form
    onsubmit={submit}
    class="relative rounded-2xl border border-[rgba(180,210,255,0.18)] bg-[#0c1220]"
  >
    <Textarea
      bind:value={input}
      disabled={busy}
      rows={1}
      placeholder="Enviá un mensaje…"
      onkeydown={onComposerKeydown}
      class="max-h-[200px] min-h-[52px] resize-none border-0 bg-transparent px-4 py-3.5 pr-14 text-[13px] text-[#eaf1ff] shadow-none placeholder:text-[#8a96ad] focus-visible:ring-0 dark:bg-transparent"
    />
    <div class="absolute right-2.5 bottom-2.5">
      {#if busy}
        <Button
          type="button"
          size="icon"
          variant="outline"
          aria-label="Detener"
          onclick={() => chat.stop()}
          class="size-9 rounded-full"
        >
          <Square class="size-3.5" />
        </Button>
      {:else}
        <Button
          type="submit"
          size="icon"
          aria-label="Enviar"
          disabled={!canSend}
          class="size-9 rounded-full bg-gradient-to-b from-[rgba(198,220,255,0.92)] to-[rgba(159,197,255,0.92)] text-[#06121c] hover:scale-105 disabled:opacity-40"
        >
          <Send class="size-3.5" />
        </Button>
      {/if}
    </div>
  </form>
</div>
