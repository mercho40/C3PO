<script lang="ts">
  import { onMount } from "svelte";
  import { replaceState } from "$app/navigation";
  import { createApi } from "$lib/api";
  import { Chat } from "@ai-sdk/svelte";
  import {
    isToolUIPart,
    getToolOrDynamicToolName,
    DefaultChatTransport,
  } from "ai";
  import { PUBLIC_API_URL } from "$env/static/public";
  import { PanelLeft, RotateCw } from "@lucide/svelte";

  import { Button } from "$lib/components/ui/button/index.js";
  import * as Sheet from "$lib/components/ui/sheet/index.js";
  import * as Conversation from "$lib/components/ai-elements/conversation/index.js";
  import * as Message from "$lib/components/ai-elements/message/index.js";
  import * as PromptInput from "$lib/components/ai-elements/prompt-input/index.js";
  import * as Tool from "$lib/components/ai-elements/tool/index.js";
  import * as Reasoning from "$lib/components/ai-elements/reasoning/index.js";
  import { Response } from "$lib/components/ai-elements/response/index.js";
  import { Suggestion } from "$lib/components/ai-elements/suggestion/index.js";
  import { Loader } from "$lib/components/ai-elements/loader/index.js";
  import ChatHistory from "$lib/components/chat-history.svelte";
  import type { PageData } from "./$types";

  let { data }: { data: PageData } = $props();

  // Talks to the backend internal agent (POST /agent), which streams the
  // model's tokens + tool calls back as a UI message stream and persists both
  // sides of the turn.
  const transport = new DefaultChatTransport({
    api: `${PUBLIC_API_URL}/agent`,
    credentials: "include",
  });

  // Id for a conversation that doesn't exist yet. Generated once per mount so
  // it stays stable across re-renders; sent with every turn so the stream and
  // the row it persists to agree without a round-trip.
  const draftId = crypto.randomUUID();

  const chatId = $derived(data.selected?.id ?? draftId);

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

  const busy = $derived(
    chat.status === "submitted" || chat.status === "streaming",
  );

  let historyOpen = $state(false);

  const suggestions = [
    "¿Cuál es el estado del robot?",
    "Caminá 2 metros hacia adelante",
    "Saludá con la mano",
    "Pará todo movimiento",
  ];

  function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    chat.sendMessage({ text: trimmed });
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

  /**
   * The bridge reports a failed skill as a *successful* tool result carrying an
   * `error` key — `apps/back/src/agent/runtime.ts` does that on purpose, so the
   * agent can read the failure and recover instead of the stream aborting. Left
   * alone, the UI would badge a skill that never ran with a green "Completado"
   * next to a paragraph explaining that it failed. Unwrap it so the card agrees
   * with the prose.
   */
  function toolFailure(output: unknown): string | null {
    if (output && typeof output === "object" && "error" in output) {
      const { error } = output as { error: unknown };
      if (typeof error === "string" && error.length > 0) return error;
    }
    return null;
  }

  // A conversation started from scratch is written to the database by the first
  // turn, but the rail was rendered from the server load and still says there
  // are none.
  //
  // `invalidateAll()` is the obvious move and the wrong one: re-running the page
  // load repopulates `data.selected`, which rebuilds the derived `Chat` — and
  // the transcript the operator is reading blanks out mid-conversation. So fetch
  // the row for this one chat and prepend it locally.
  let createdChat = $state<(typeof data.chats)[number] | null>(null);

  const history = $derived.by(() => {
    const server = data.chats ?? [];
    // Drop the local row the moment the server knows about it, or as soon as
    // this conversation is no longer the open one — so it can't outlive a
    // deletion or linger into another chat.
    if (
      !createdChat ||
      createdChat.id !== chatId ||
      server.some((c) => c.id === createdChat!.id)
    ) {
      return server;
    }
    return [createdChat, ...server];
  });

  let listRefreshed = $state(false);
  $effect(() => {
    if (
      !data.selected &&
      !listRefreshed &&
      chat.status === "ready" &&
      chat.messages.length > 0
    ) {
      listRefreshed = true;
      const id = chatId;
      // Take the server's own title rather than guessing one from the prompt —
      // the backend derives it in `titleFromParts`, and two different titles for
      // the same conversation is worse than a moment's delay.
      void createApi(fetch)
        .chats.get({ query: {} })
        .then(({ data: res, error }) => {
          if (error) return;
          const row = res?.chats?.find((c) => c.id === id);
          if (row) createdChat = row;
        });
    }
  });

  // While a turn is in flight the last assistant message may still be empty
  // (the model is thinking, or the first tool call hasn't resolved). Show a
  // pulse so the composer's disabled state isn't the only feedback.
  const awaitingFirstToken = $derived(
    chat.status === "submitted" ||
      (chat.status === "streaming" &&
        chat.messages.at(-1)?.role !== "assistant"),
  );
</script>

<div class="flex h-full min-h-0 gap-4">
  <!-- Conversation rail. Hidden below lg, where it becomes a sheet. -->
  <aside class="hidden w-64 shrink-0 flex-col panel p-3 lg:flex">
    <ChatHistory chats={history} activeId={chatId} />
  </aside>

  <Sheet.Root bind:open={historyOpen}>
    <Sheet.Content side="left" class="w-[280px] p-3">
      <Sheet.Header class="px-1 pb-2">
        <Sheet.Title class="text-sm">Conversaciones</Sheet.Title>
      </Sheet.Header>
      <ChatHistory
        chats={history}
        activeId={chatId}
        onnavigate={() => (historyOpen = false)}
      />
    </Sheet.Content>
  </Sheet.Root>

  <!-- Conversation column -->
  <div class="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
    <!-- Opens the sheet by driving its bound state directly; Sheet.Trigger
         would need to live inside Sheet.Root to pick up its context. -->
    <Button
      variant="outline"
      size="sm"
      onclick={() => (historyOpen = true)}
      class="w-fit gap-2 tile-interactive text-ink lg:hidden"
    >
      <PanelLeft class="size-4" />
      Conversaciones
    </Button>

    <div class="relative flex min-h-0 flex-1 flex-col overflow-hidden panel">
      <Conversation.Root class="min-h-0 flex-1">
        <Conversation.Content class="min-h-0 flex-1 gap-0 overflow-y-auto p-0">
          <!-- With nothing said yet there's a whole panel of empty space, so the
               prompt centres in it rather than clinging to the top edge. -->
          <div
            class="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6 sm:px-6 {chat
              .messages.length === 0
              ? 'min-h-full justify-center'
              : ''}"
          >
            {#if chat.messages.length === 0}
              <div class="flex flex-col items-center gap-6 text-center">
                <img
                  src="/logo.svg"
                  alt=""
                  class="size-14 object-contain drop-shadow-[0_0_18px_rgba(126,229,255,0.5)]"
                />
                <div class="max-w-md">
                  <p class="stamp-quiet text-xl text-ink">Hablá con el robot</p>
                  <p class="mt-1.5 text-sm text-ink-mute">
                    Pedile estados, movimientos o gestos. El agente decide qué
                    habilidades ejecutar.
                  </p>
                </div>
                <!-- Wraps rather than scrolls: the registry's <Suggestions>
                     puts these in a horizontal ScrollArea, which clipped the
                     last one at the panel edge with no visible affordance. -->
                <div class="flex flex-wrap justify-center gap-2">
                  {#each suggestions as s (s)}
                    <Suggestion
                      suggestion={s}
                      onclick={send}
                      disabled={busy}
                      class="h-auto tile-interactive py-1.5 whitespace-normal text-ink-dim hover:text-ink"
                    />
                  {/each}
                </div>
              </div>
            {:else}
              {#each chat.messages as message (message.id)}
                <Message.Root from={message.role}>
                  <Message.Content
                    class="group-[.is-user]:border group-[.is-user]:border-accent-edge group-[.is-user]:bg-accent group-[.is-user]:text-ink"
                  >
                    {#each message.parts as part, i (i)}
                      {#if part.type === "text"}
                        <Response content={part.text} />
                      {:else if part.type === "reasoning"}
                        <Reasoning.Root
                          class="w-full"
                          isStreaming={chat.status === "streaming"}
                        >
                          <Reasoning.Trigger />
                          <Reasoning.Content content={part.text} />
                        </Reasoning.Root>
                      {:else if isToolUIPart(part)}
                        {@const failure =
                          part.state === "output-error"
                            ? (part.errorText ?? "Error desconocido")
                            : part.state === "output-available"
                              ? toolFailure(part.output)
                              : null}
                        <!-- Expanded while running, or when it failed, so the
                             operator sees which skill is driving the robot right
                             now and why one didn't; collapsed on success, to
                             keep long transcripts scannable. -->
                        <Tool.Root
                          class="border-hairline bg-wash"
                          open={part.state === "input-available" ||
                            failure !== null}
                        >
                          <Tool.Header
                            type={getToolOrDynamicToolName(part)}
                            state={failure ? "output-error" : part.state}
                          />
                          <Tool.Content>
                            <Tool.Input input={part.input} />
                            <Tool.Output
                              output={failure ||
                              part.state !== "output-available"
                                ? undefined
                                : part.output}
                              errorText={failure ?? undefined}
                            />
                          </Tool.Content>
                        </Tool.Root>
                      {/if}
                    {/each}
                  </Message.Content>
                </Message.Root>
              {/each}

              {#if awaitingFirstToken}
                <div class="flex items-center gap-2.5 text-ink-mute">
                  <Loader size={14} />
                  <span class="readout">Pensando…</span>
                </div>
              {/if}
            {/if}
          </div>
        </Conversation.Content>
        <Conversation.ScrollButton
          class="border-hairline-strong bg-panel-lift text-ink hover:bg-panel-lift"
        />
      </Conversation.Root>
    </div>

    {#if chat.error}
      <div
        class="flex items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger/[0.06] px-4 py-2.5 text-xs text-danger-soft"
      >
        <span class="truncate">{chat.error.message}</span>
        <Button
          variant="outline"
          size="sm"
          class="h-7 shrink-0 gap-1.5"
          onclick={() => chat.regenerate()}
        >
          <RotateCw class="size-3" />
          Reintentar
        </Button>
      </div>
    {/if}

    <PromptInput.Root
      onSubmit={(message) => send(message.text)}
      class="panel shadow-none"
    >
      <PromptInput.Body>
        <PromptInput.Textarea
          placeholder="Enviá un mensaje…"
          class="text-sm text-ink placeholder:text-ink-mute"
        />
        <PromptInput.Toolbar class="border-t border-hairline px-3 py-2">
          <span class="hidden readout sm:inline">
            Enter para enviar · Shift + Enter para salto de línea
          </span>
          <PromptInput.Submit
            status={chat.status}
            onStop={() => chat.stop()}
            class="ms-auto size-9 rounded-full"
          />
        </PromptInput.Toolbar>
      </PromptInput.Body>
    </PromptInput.Root>
  </div>
</div>
