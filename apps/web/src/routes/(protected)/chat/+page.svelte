<script lang="ts">
  import { onMount, tick } from "svelte";
  import { replaceState } from "$app/navigation";
  import { Chat } from "@ai-sdk/svelte";
  import {
    DefaultChatTransport,
    isToolOrDynamicToolUIPart,
    getToolOrDynamicToolName,
  } from "ai";
  import { PUBLIC_API_URL } from "$env/static/public";
  import { Send, Wrench, Square, Plus, MessageSquare } from "@lucide/svelte";
  import { ScrollArea } from "$lib/components/ui/scroll-area/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Textarea } from "$lib/components/ui/textarea/index.js";
  import Markdown from "$lib/components/markdown.svelte";
  import type { PageData } from "./$types";

  let { data }: { data: PageData } = $props();

  // Talks to the backend internal agent (POST /agent), which streams Claude's
  // tokens + tool calls back as a UI message stream and persists both sides of
  // the turn.
  const transport = new DefaultChatTransport({
    api: `${PUBLIC_API_URL}/agent`,
    credentials: "include",
  });

  // Id for a conversation that doesn't exist yet. Generated once per mount so
  // it stays stable across re-renders; sent with every turn so the stream and
  // the row it persists to agree without a round-trip.
  const draftId = crypto.randomUUID();

  const chatId = $derived(data.selected?.id ?? draftId);
  const history = $derived(data.chats ?? []);

  // Derived, not constructed once: SvelteKit reuses this component when only
  // the query string changes, so building the Chat from the initial `data`
  // would leave the previous conversation on screen after switching chats.
  // Deriving rebuilds it when the selected id changes and leaves it alone
  // while streaming (nothing invalidates `data` mid-turn).
  //
  // `messages` rehydrates history from the database; because we store
  // `UIMessage.parts` verbatim, this reproduces tool-call cards exactly as they
  // streamed rather than a flattened transcript.
  const chat = $derived.by(
    () =>
      new Chat({
        id: data.selected?.id ?? draftId,
        messages: (data.selected?.messages ?? []) as never,
        transport,
      }),
  );

  let input = $state("");
  let bottomEl = $state<HTMLDivElement>();

  const busy = $derived(
    chat.status === "submitted" || chat.status === "streaming",
  );
  const canSend = $derived(input.trim().length > 0 && !busy);

  const suggestions = [
    {
      title: "Estado del robot",
      label: "postura, batería y fallos",
      action: "¿Cuál es el estado del robot?",
    },
    {
      title: "Caminá",
      label: "2 metros hacia adelante",
      action: "Caminá 2 metros hacia adelante",
    },
    {
      title: "Pará todo",
      label: "detener el movimiento",
      action: "Pará todo movimiento",
    },
    {
      title: "Saludá",
      label: "hacé un gesto con la mano",
      action: "Saludá con la mano",
    },
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

  /** Put `?id=` in the URL without navigating, so a reload resumes this chat. */
  function pinChatToUrl() {
    const params = new URLSearchParams(window.location.search);
    // Drop `q` — it's a one-shot hand-off; resending it on reload would
    // silently re-issue a robot command.
    params.delete("q");
    params.set("id", chatId);
    replaceState(`/chat?${params}`, {});
  }

  // Hand-off from the dashboard command box / quick controls: an initial prompt
  // arrives as `?q=…`. Send it once, then rewrite the query so a reload doesn't
  // resend it — while keeping the chat id so the conversation survives.
  onMount(() => {
    const q = new URLSearchParams(window.location.search).get("q")?.trim();
    if (q) {
      chat.sendMessage({ text: q });
      pinChatToUrl();
    }
  });

  // A brand-new chat has no `?id=` until it has actually said something. Pin it
  // on the first message so the row exists before the URL advertises it —
  // pinning earlier would hand out a link to a chat that was never created.
  $effect(() => {
    if (chat.messages.length > 0 && !data.selected) {
      const current = new URLSearchParams(window.location.search).get("id");
      if (current !== chatId) pinChatToUrl();
    }
  });

  // Follow the conversation as messages arrive / streaming starts and stops.
  $effect(() => {
    chat.messages.length;
    chat.status;
    tick().then(() => bottomEl?.scrollIntoView({ block: "end" }));
  });
</script>

<div class="flex h-full w-full flex-col gap-4">
  <!-- Conversation history. Plain links rather than client-side state: each is
       a real URL an operator can bookmark or share, and the server load has the
       messages ready on first paint. -->
  <div class="flex items-center gap-2 overflow-x-auto pb-1">
    <Button
      href="/chat"
      variant="outline"
      size="sm"
      class="shrink-0 gap-1.5 border-[rgba(180,210,255,0.14)] bg-[#0c1220] text-[13px] text-[#eaf1ff]"
    >
      <Plus class="size-3.5" />
      Nuevo chat
    </Button>

    {#each history as item (item.id)}
      {@const active = item.id === chatId}
      <a
        href={`/chat?id=${item.id}`}
        title={item.title ?? "Sin título"}
        class="flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[13px] transition-colors {active
          ? 'border-[rgba(126,229,255,0.4)] bg-[rgba(126,229,255,0.08)] text-[#eaf1ff]'
          : 'border-[rgba(180,210,255,0.1)] bg-[#0c1220] text-[#8a96ad] hover:text-[#eaf1ff]'}"
      >
        <MessageSquare class="size-3.5 shrink-0" />
        <span class="max-w-[180px] truncate">{item.title ?? "Sin título"}</span>
      </a>
    {/each}
  </div>

  <ScrollArea
    class="min-h-0 flex-1 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828]"
  >
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
              Pedile estados, movimientos o gestos. El agente decide qué
              habilidades ejecutar.
            </p>
          </div>
          <div class="grid w-full max-w-lg gap-2 sm:grid-cols-2">
            {#each suggestions as s (s.title)}
              <Button
                type="button"
                variant="outline"
                onclick={() => suggest(s.action)}
                class="flex h-auto flex-col items-start gap-0.5 rounded-xl border-[rgba(180,210,255,0.12)] bg-[rgba(180,210,255,0.02)] px-4 py-3 text-left whitespace-normal hover:border-[rgba(159,197,255,0.3)] hover:bg-[rgba(159,197,255,0.06)]"
              >
                <span class="text-[13px] text-[#eaf1ff]">{s.title}</span>
                <span class="text-[12px] text-[#8a96ad]">{s.label}</span>
              </Button>
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
            <div
              class="flex max-w-[80%] flex-col gap-1.5 {isUser
                ? 'items-end'
                : 'items-start'}"
            >
              {#each message.parts as part, i (i)}
                {#if part.type === "text"}
                  {#if isUser}
                    <div
                      class="rounded-2xl border border-[rgba(159,197,255,0.2)] bg-[rgba(159,197,255,0.14)] px-4 py-2.5 text-[13px] leading-relaxed whitespace-pre-wrap text-[#eaf1ff]"
                    >
                      {part.text}
                    </div>
                  {:else}
                    <div
                      class="rounded-2xl border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.04)] px-4 py-2.5"
                    >
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
                    {#if part.state === "output-available"}· ✓{:else if part.state === "output-error"}·
                      ✗{:else}· …{/if}
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
    <div
      class="flex items-center justify-between gap-3 rounded-[10px] border border-[rgba(255,77,106,0.3)] bg-[rgba(255,77,106,0.06)] px-4 py-2.5 text-[12px] text-[#ff8aa0]"
    >
      <span class="truncate">{chat.error.message}</span>
      <Button
        variant="outline"
        size="sm"
        class="h-7 shrink-0"
        onclick={() => chat.regenerate()}>Reintentar</Button
      >
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
