<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { afterNavigate, goto, refreshAll } from "$app/navigation";
  import { createApi } from "#lib/api.js";
  import { Chat } from "@ai-sdk/svelte";
  import { isToolUIPart, DefaultChatTransport, type UIMessage } from "ai";
  import { resolve } from "$app/paths";
  import { PUBLIC_API_URL } from "$app/env/public";
  import { MessagesSquare, Plus } from "@lucide/svelte";
  import { toast } from "svelte-sonner";

  import { Button } from "#lib/components/ui/button/index.js";
  import * as Sheet from "#lib/components/ui/sheet/index.js";
  import * as Conversation from "#lib/components/ai-elements/conversation/index.js";
  import * as Message from "#lib/components/ai-elements/message/index.js";
  import * as PromptInput from "#lib/components/ai-elements/prompt-input/index.js";
  import * as Reasoning from "#lib/components/ai-elements/reasoning/index.js";
  import { Response } from "#lib/components/ai-elements/response/index.js";
  import { Suggestion } from "#lib/components/ai-elements/suggestion/index.js";
  import { Loader } from "#lib/components/ai-elements/loader/index.js";
  import { CopyButton } from "#lib/components/ai-elements/copy-button/index.js";
  import ChatHistory from "#lib/components/chat-history.svelte";
  import Callout from "#lib/components/callout.svelte";
  import ToolCard from "#lib/components/chat/tool-card.svelte";
  import ChatTitle from "#lib/components/chat/chat-title.svelte";
  import ChatErrorBanner from "#lib/components/chat/chat-error-banner.svelte";
  import { redirectToLogin } from "#lib/robot/poller.svelte.js";
  import { LINK } from "#lib/robot/link-copy.js";
  import {
    awaitingModel,
    continuationPrompt,
    planRecovery,
    replyText,
    type RecoveryPlan,
  } from "#lib/chat/turn.js";
  import { describeChatError } from "#lib/chat/chat-error.js";
  import {
    shouldPollTranscript,
    transcriptFreshness,
    TRANSCRIPT_POLL_MS,
  } from "#lib/chat/transcript.js";
  import {
    canLoadMoreHistory,
    HISTORY_MAX,
    HISTORY_PAGE,
    shouldPinDraft,
  } from "#lib/chat/history.js";
  import { newId } from "#lib/chat/ids.js";
  import { TranscriptPoller } from "./transcript-poller.svelte";
  import type { PageData } from "./$types";

  let { data }: { data: PageData } = $props();

  let sessionExpired = $state(false);
  /**
   * HTTP status of the last POST /agent that failed before streaming. The SDK
   * folds every failure into `new Error(responseBody)`, which loses whether a
   * bare "Conflict" was a read-only voice chat (409) or a gateway that is down
   * (502) — and those need different advice.
   */
  let failedStatus = $state<number | null>(null);

  // Talks to the backend internal agent (POST /agent), which streams the
  // model's tokens + tool calls back as a UI message stream and persists both
  // sides of the turn. A generic "Failed to fetch"-style error from the AI SDK
  // doesn't tell an operator whether their session expired or the robot link is
  // down -- intercept 401 specifically here (before the SDK's own error
  // handling swallows the distinction into one opaque error) and send them to
  // re-auth, back to this chat afterwards, instead of offering a retry that
  // would just fail the same way again.
  const transport = new DefaultChatTransport({
    api: `${PUBLIC_API_URL}/agent`,
    credentials: "include",
    fetch: async (input, init) => {
      failedStatus = null;
      const res = await fetch(input, init);
      if (!res.ok) failedStatus = res.status;
      if (res.status === 401) {
        sessionExpired = true;
        redirectToLogin("expired");
      }
      return res;
    },
  });

  // Id for a conversation that doesn't exist yet. Stable across re-renders, so
  // the stream and the row it persists to agree without a round-trip.
  // `newId`, not `crypto.randomUUID()`: that one only exists on https or
  // localhost, and on a phone at http://<lan-ip>:3001 this line threw during
  // hydration and took the whole page with it.
  let draftId = $state(newId());

  // Two facts about the draft id, neither rendered (so neither is `$state`):
  //  - spent: a message went out under it, so the backend may now hold a row
  //    by that id and it must never name a second conversation — even when
  //    the send failed and nothing is pinned;
  //  - pinned: it is in the URL. Only after the backend accepted a turn.
  // They used to be one flag, set on the first message, which is how a send
  // to a dead backend still advertised `?id=` for a row that never existed.
  let draftSpent = false;
  let draftPinned = false;

  const chatId = $derived(data.selected?.id ?? draftId);

  // Keyed on the chat id and NOTHING else. SvelteKit reuses this component
  // when only the query string changes, so building the Chat once from the
  // initial `data` would leave the previous conversation on screen after
  // switching chats — it has to be rebuilt when the id changes. But it used to
  // be derived from the whole `data.selected` object, so ANY re-run of the
  // load (a delete in the rail called `invalidateAll()`) built a fresh Chat
  // from the database copy mid-turn: the streaming reply vanished, the old
  // instance kept streaming (and the robot kept executing) unseen, and the
  // composer's Stop no longer reached it. The messages are read untracked.
  //
  // `messages` rehydrates history from the database; because we store
  // `UIMessage.parts` verbatim, this reproduces tool-call cards exactly as they
  // streamed rather than a flattened transcript.
  const chat = $derived.by(() => {
    const id = chatId;
    return untrack(
      () =>
        new Chat({
          id,
          messages: (data.selected?.id === id
            ? data.selected.messages
            : []) as unknown as UIMessage[],
          transport,
        }),
    );
  });

  // When the id does change mid-turn (back/forward, an edited URL — the rail
  // holds its links while busy), stop the instance being replaced rather than
  // leave it streaming into a page nobody can see. Stopping the stream does
  // not stop a bridge call already running; that is what "Parar" is for.
  let previousChat: Chat | null = null;
  $effect(() => {
    const current = chat;
    const previous = previousChat;
    previousChat = current;
    if (previous && previous !== current) {
      untrack(() => {
        if (
          previous.status === "submitted" ||
          previous.status === "streaming"
        ) {
          void previous.stop();
        }
      });
    }
  });

  const busy = $derived(
    chat.status === "submitted" || chat.status === "streaming",
  );

  // And when the PAGE goes away mid-turn. The rail holds its links while busy,
  // but the console's sidebar, the topbar and the browser's Back do not — so
  // leaving /chat used to leave the agent streaming, and calling robot tools,
  // into a page nobody could see, with no Stop left to press. The instance is
  // read from `previousChat` (always the current one, see above) rather than
  // the derived, which is being torn down with the component. This stops the
  // reply, not the robot: a bridge call already running is Parar's job, and
  // the toast says so.
  $effect(() => () => {
    const current = previousChat;
    if (
      current &&
      (current.status === "submitted" || current.status === "streaming")
    ) {
      void current.stop();
      toast.warning("Se detuvo la respuesta del agente", {
        description:
          "Saliste del chat mientras respondía. Esto no frena al robot: si se está moviendo, usá Parar (Shift+Espacio).",
      });
    }
  });

  // ---- Voice transcripts ---------------------------------------------------

  // A voice chat is written by the realtime engine while its session runs; a
  // load-once copy is stale the moment the robot says anything else. Re-fetch
  // it while visible (see `#lib/chat/transcript.ts` for why this is safe).
  const isVoice = $derived(data.selected?.channel === "voice");
  const transcript = $derived.by(() => {
    const selected = data.selected;
    if (
      !selected ||
      !shouldPollTranscript({
        channel: selected.channel,
        selectedStatus: data.selectedStatus,
      })
    ) {
      return null;
    }
    return untrack(
      () =>
        new TranscriptPoller(selected.id, selected.messages, selected.title),
    );
  });

  $effect(() => {
    const poller = transcript;
    if (!poller) return;
    poller.start(TRANSCRIPT_POLL_MS);
    return () => poller.stop();
  });

  // Drives "Actualizado hace N s" only while a transcript is on screen.
  let now = $state(Date.now());
  $effect(() => {
    if (!transcript) return;
    const timer = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(timer);
  });

  const freshness = $derived(
    transcript
      ? transcriptFreshness({
          lastOkAt: transcript.lastOkAt,
          failures: transcript.failures,
          gone: transcript.gone,
          now,
        })
      : null,
  );

  /** What the transcript shows: the live poll for voice, the Chat otherwise. */
  const messages = $derived(transcript ? transcript.messages : chat.messages);

  // ---- History rail --------------------------------------------------------

  // The rail is local state seeded from the load (a writable derived: a new
  // load replaces it). Renames and deletes edit it in place instead of
  // re-running the load — see the comment on `chat` for what that used to do.
  let rows = $derived(data.chats ?? []);
  let historyError = $derived(data.historyError);
  let localTitle = $state<{ id: string; title: string } | null>(null);

  const currentRow = $derived(rows.find((r) => r.id === chatId) ?? null);
  const title = $derived(
    (localTitle?.id === chatId ? localTitle.title : null) ??
      transcript?.title ??
      currentRow?.title ??
      data.selected?.title ??
      null,
  );
  // A draft has no row until its first message reaches the backend.
  const renamable = $derived(Boolean(data.selected) || currentRow !== null);

  // The backend lists 50 by default and at most 200. "Ver más" asks for the
  // next page's worth by raising the limit; a new load starts over at one page.
  let historyLimit = $derived.by(() => {
    void data.chats;
    return HISTORY_PAGE;
  });
  // How many rows the last list request returned — NOT `rows.length`, which
  // a local delete shrinks (see `canLoadMoreHistory`).
  let historyFetched = $derived(data.chats?.length ?? 0);
  const canLoadMore = $derived(
    canLoadMoreHistory({
      historyError,
      fetched: historyFetched,
      limit: historyLimit,
    }),
  );

  async function refreshHistory(limit = historyLimit): Promise<void> {
    try {
      const { data: res, error } = await createApi(fetch).chats.get({
        query: limit === HISTORY_PAGE ? {} : { limit },
      });
      if (error) {
        if (error.status === 401) redirectToLogin("expired");
        else historyError = true;
        return;
      }
      rows = res?.chats ?? [];
      historyFetched = rows.length;
      historyLimit = limit;
      historyError = false;
    } catch {
      historyError = true;
    }
  }

  function loadMoreHistory(): Promise<void> {
    return refreshHistory(Math.min(historyLimit + HISTORY_PAGE, HISTORY_MAX));
  }

  function renamed(id: string, newTitle: string) {
    // The backend bumps `updatedAt` on rename and lists newest first; mirror
    // that so the rail doesn't reorder on the next load.
    const row = rows.find((r) => r.id === id);
    if (row) {
      rows = [
        { ...row, title: newTitle, updatedAt: new Date() },
        ...rows.filter((r) => r.id !== id),
      ];
    }
    if (id === chatId) localTitle = { id, title: newTitle };
  }

  function deleted(id: string) {
    rows = rows.filter((r) => r.id !== id);
  }

  let historyOpen = $state(false);

  // ---- Composer ------------------------------------------------------------

  let draft = $state("");
  let composer = $state<HTMLTextAreaElement | null>(null);

  // Read-only status questions and motion alike FILL the composer; nothing is
  // sent on one click. "Caminá 2 metros" used to reach the real G1 the moment
  // the chip was touched. And there is no "stop" chip: routing an emergency
  // stop through the slowest, least reliable path in the console (an LLM
  // gateway that takes ~6 s to fail) trained exactly the wrong reflex. The
  // empty state points at the header's Parar instead.
  const suggestions = [
    "¿Cuál es el estado del robot?",
    "¿Cuánta batería le queda?",
    "Caminá 2 metros hacia adelante",
    "Saludá con la mano",
  ];

  async function fillComposer(text: string) {
    draft = text;
    await tick();
    composer?.focus();
    composer?.setSelectionRange(text.length, text.length);
  }

  /**
   * Refusing must REJECT. PromptInput clears the textarea whenever onSubmit
   * resolves; returning quietly on "busy" is how an operator's next command
   * used to vanish when they pressed Enter while the agent was replying.
   * Not awaited: `sendMessage` resolves only when the whole turn ends, and the
   * composer should clear as soon as the message is on its way.
   */
  function send(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return Promise.reject(new Error("Mensaje vacío"));
    if (busy) {
      return Promise.reject(new Error("El agente todavía está respondiendo"));
    }
    if (isVoice) {
      return Promise.reject(new Error("Transcripción de voz: solo lectura"));
    }
    if (!data.selected) draftSpent = true;
    void chat.sendMessage({ text: trimmed });
    return Promise.resolve();
  }

  // ---- Failure recovery ----------------------------------------------------

  const errorView = $derived(
    chat.error ? describeChatError(chat.error.message, failedStatus) : null,
  );
  const recovery = $derived(chat.error ? planRecovery(chat.messages) : null);

  function retry() {
    // `regenerate()` drops the failed reply on the client, so the server sees a
    // history ending in the user's message and mints a NEW id for the next
    // reply — while the partial one it already saved from `onEnd` stays, and a
    // reload shows both. Name the reply being replaced so the backend can
    // overwrite that row; routes/agent.ts ignores the field until it does.
    const last = chat.messages.at(-1);
    const replaceMessageId = last?.role === "assistant" ? last.id : undefined;
    void chat.regenerate(
      replaceMessageId ? { body: { replaceMessageId } } : undefined,
    );
  }

  function continueTurn(plan: Extract<RecoveryPlan, { kind: "continue" }>) {
    void chat.sendMessage({ text: continuationPrompt(plan) });
  }

  // ---- URL and draft bookkeeping ---------------------------------------------

  /**
   * Rewrite the query without navigating: always drop `q` — it's a one-shot
   * hand-off; resending it on reload would silently re-issue a robot command —
   * and, with `pin`, add `?id=` so a reload resumes this chat.
   */
  function rewriteUrl(pin: boolean) {
    const params = new URLSearchParams(window.location.search);
    params.delete("q");
    if (pin) {
      draftPinned = true;
      params.set("id", chatId);
    }
    const query = params.toString();
    const url = resolve(query ? `chat?${query}` : "chat");
    void goto(url, { shallow: true, replace: true });
  }

  // Hand-off from the dashboard command box / quick controls: an initial prompt
  // arrives as `?q=…`. Send it once, then strip it so a reload doesn't resend
  // it. The id is pinned by the effect below once the backend has accepted the
  // turn — not here, before anyone knows whether it will.
  //
  // The rewrite waits a task: on a direct load this runs during hydration,
  // before SvelteKit's router has started, and a shallow `goto` throws there.
  onMount(() => {
    const q = new URLSearchParams(window.location.search).get("q")?.trim();
    if (q && !isVoice) {
      draftSpent = true;
      void chat.sendMessage({ text: q });
      const timer = setTimeout(() => rewriteUrl(false));
      return () => clearTimeout(timer);
    }
  });

  // Whether the rail has been refreshed for the current draft. Not `$state`:
  // only the effect below reads it.
  let listRefreshed = false;

  // "Nuevo chat" is a link to /chat, and the router answers it by re-running the
  // load against this same component instance — `data.selected` goes null but
  // the instance, and its draft id, survive. Reusing a spent id would make the
  // backend append the new conversation onto the old one's row: two unrelated
  // transcripts in a single chat, visible the moment either is reloaded. Mint a
  // fresh one instead.
  //
  // Except on the FIRST call, which is the navigation that mounted this page:
  // the draft is brand new — or a `?q=` hand-off spent it a moment ago, on
  // purpose, in onMount, which runs before this. Minting there threw the
  // dashboard's command away into a Chat nobody could see.
  //
  // And never on `rewriteUrl`'s own shallow `goto`: SvelteKit 3 reports those
  // to afterNavigate too, and the `?q=` hand-off spends the draft right before
  // rewriting, so minting there would throw its command away the same way.
  let arrived = false;
  afterNavigate(({ shallow }) => {
    if (shallow) return;
    if (!arrived) {
      arrived = true;
      return;
    }
    if (!data.selected && draftSpent) {
      draftId = newId();
      draftSpent = false;
      draftPinned = false;
      listRefreshed = false;
    }
  });

  // A brand-new chat has no `?id=` until the backend has taken its first turn
  // — a reply streaming, or one already there. Pinning earlier hands out a link
  // to a chat that may never be created: with back down, the first send fails
  // and the row does not exist. Once per draft: see `shouldPinDraft` for the
  // "Nuevo chat" race that used to write the previous conversation's id back.
  $effect(() => {
    const accepted =
      chat.status === "streaming" ||
      chat.messages.some((m) => m.role === "assistant");
    if (
      shouldPinDraft({
        pinned: draftPinned,
        messageCount: chat.messages.length,
        hasSelected: data.selected !== null,
        accepted,
      })
    ) {
      rewriteUrl(true);
    }
  });

  // A conversation started from scratch is written to the database by its
  // first turn, but the rail was rendered from the server load and doesn't
  // list it. Re-read the list once the turn settles — the server's own title
  // (`titleFromParts`) rather than a guess from the prompt, since two titles
  // for one conversation is worse than a moment's delay.
  $effect(() => {
    if (!data.selected && !listRefreshed && !busy && chat.messages.length > 0) {
      listRefreshed = true;
      void refreshHistory();
    }
  });

  const thinking = $derived(awaitingModel(chat.status, chat.messages));

  /** The `?id=` asked for couldn't be opened, and nothing has been said since. */
  const loadNotice = $derived(
    !data.selected && chat.messages.length === 0
      ? data.selectedStatus === "not-found" || data.selectedStatus === "error"
        ? data.selectedStatus
        : null
      : null,
  );
</script>

<div class="flex h-full min-h-0 gap-4">
  <!-- Conversation rail. Hidden below lg, where it becomes a sheet. -->
  <aside
    aria-label="Conversaciones"
    class="hidden w-64 shrink-0 flex-col panel p-3 lg:flex"
  >
    <ChatHistory
      chats={rows}
      activeId={chatId}
      {busy}
      {historyError}
      {canLoadMore}
      onretry={() => refreshHistory()}
      onloadmore={loadMoreHistory}
      ondeleted={deleted}
      onrenamed={renamed}
    />
  </aside>

  <Sheet.Root bind:open={historyOpen}>
    <Sheet.Content side="left" class="w-[280px] p-3">
      <Sheet.Header class="px-1 pb-2">
        <Sheet.Title class="text-sm">Conversaciones</Sheet.Title>
      </Sheet.Header>
      <ChatHistory
        chats={rows}
        activeId={chatId}
        {busy}
        {historyError}
        {canLoadMore}
        onretry={() => refreshHistory()}
        onloadmore={loadMoreHistory}
        ondeleted={deleted}
        onrenamed={renamed}
        onnavigate={() => (historyOpen = false)}
      />
    </Sheet.Content>
  </Sheet.Root>

  <!-- Conversation column -->
  <div class="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
    <!-- `flex-wrap`: the freshness line drops under the title when both do not
         fit. It was `shrink-0` beside it, and "No se pudo actualizar · última
         copia hace 12 s" pushed the row past the edge of a 360px screen. -->
    <div class="flex min-h-9 flex-wrap items-center gap-x-2 gap-y-1">
      <!-- Opens the sheet by driving its bound state directly; Sheet.Trigger
           would need to live inside Sheet.Root to pick up its context.
           Not the panel glyph, and never icon-only: on a phone this sat right
           under the topbar's menu button with the same PanelLeft icon, and
           two identical buttons stacked read as one control drawn twice. -->
      <Button
        variant="outline"
        size="sm"
        onclick={() => (historyOpen = true)}
        class="shrink-0 gap-2 tile-interactive text-ink lg:hidden"
      >
        <MessagesSquare class="size-4" aria-hidden="true" />
        <!-- The visible word is the accessible name at each width, so voice
             control's "tocá Chats" matches what is on screen. -->
        <span class="sm:hidden">Chats</span>
        <span class="hidden sm:inline">Conversaciones</span>
      </Button>
      <div class="flex min-w-[min(100%,10rem)] flex-1">
        {#key chatId}
          <ChatTitle id={chatId} {title} {renamable} onrenamed={renamed} />
        {/key}
      </div>
      {#if freshness}
        <span
          class={[
            "max-w-full min-w-0 truncate readout",
            freshness.tone === "warn" && "text-warn",
          ]}
        >
          {freshness.text}
        </span>
      {/if}
      <!-- The announcement is its own region, present for as long as a
           transcript is open, and says only what changed STATE — the visible
           line ticks every second and used to carry the live role itself. -->
      {#if transcript}
        <span role="status" class="sr-only">{freshness?.announce ?? ""}</span>
      {/if}
    </div>

    <div class="relative flex min-h-0 flex-1 flex-col overflow-hidden panel">
      <Conversation.Root class="min-h-0 flex-1">
        <Conversation.Content class="min-h-0 flex-1 gap-0 overflow-y-auto p-0">
          <!-- With nothing said yet there's a whole panel of empty space, so the
               prompt centres in it rather than clinging to the top edge. -->
          <div
            class={[
              "mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6 sm:px-6",
              messages.length === 0 && "min-h-full justify-center",
            ]}
          >
            {#if loadNotice === "not-found"}
              <Callout tone="warn" title="No encontramos esa conversación">
                Puede que la hayan eliminado o que sea de otra cuenta. Podés
                empezar una nueva acá.
              </Callout>
            {:else if loadNotice === "error"}
              <Callout tone="danger" title="No se pudo cargar la conversación">
                El servidor no respondió. Esto no es un chat vacío: la
                conversación puede existir.
                {#snippet action()}
                  <Button
                    variant="outline"
                    size="sm"
                    onclick={() => refreshAll()}
                  >
                    Reintentar
                  </Button>
                {/snippet}
              </Callout>
            {/if}

            {#if messages.length === 0}
              {#if isVoice}
                <p class="text-center text-sm text-ink-mute">
                  Esta transcripción todavía no tiene mensajes.
                </p>
              {:else}
                <div class="flex flex-col items-center gap-6 text-center">
                  <img
                    src="/logo.svg"
                    alt=""
                    class="size-14 object-contain glow-cyan"
                  />
                  <div class="max-w-md">
                    <p class="stamp-quiet text-xl text-ink">
                      Hablá con el robot
                    </p>
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
                        onclick={fillComposer}
                        class="h-auto tile-interactive py-1.5 whitespace-normal text-ink-dim hover:text-ink"
                      />
                    {/each}
                  </div>
                  <p class="max-w-md text-xs text-ink-mute">
                    Las sugerencias completan el mensaje: revisalo antes de
                    enviarlo. Para frenar el robot usá <strong
                      class="text-ink-dim">Parar</strong
                    >
                    en la barra superior (<kbd
                      class="rounded border border-hairline-strong px-1 font-mono text-2xs"
                      >Shift</kbd
                    >
                    +
                    <kbd
                      class="rounded border border-hairline-strong px-1 font-mono text-2xs"
                      >Espacio</kbd
                    >), no el chat.
                  </p>
                </div>
              {/if}
            {:else}
              {#each messages as message, mi (message.id)}
                {@const live = busy && mi === messages.length - 1}
                <Message.Root from={message.role}>
                  <Message.Content
                    class="group-[.is-user]:border group-[.is-user]:border-accent-edge group-[.is-user]:bg-accent group-[.is-user]:text-ink"
                  >
                    {#each message.parts as part, i (i)}
                      {#if part.type === "text"}
                        {#if message.role === "user"}
                          <!-- Verbatim: an operator's Shift+Enter line breaks
                               and stray `#`/`*` are not markdown. -->
                          <p class="break-words whitespace-pre-wrap">
                            {part.text}
                          </p>
                        {:else}
                          <Response content={part.text} />
                        {/if}
                      {:else if part.type === "reasoning"}
                        <!-- Live only if it is the part being written right
                             now; every saved block used to go live again on
                             each new turn. -->
                        <Reasoning.Root
                          class="w-full"
                          isStreaming={live && i === message.parts.length - 1}
                        >
                          <Reasoning.Trigger />
                          <Reasoning.Content content={part.text} />
                        </Reasoning.Root>
                      {:else if isToolUIPart(part)}
                        <ToolCard {part} {live} />
                      {/if}
                    {/each}
                  </Message.Content>
                  {#if message.role === "assistant" && !live}
                    {@const text = replyText(message.parts)}
                    {#if text}
                      <Message.Actions class="-mt-1">
                        <CopyButton
                          {text}
                          label="Copiar respuesta"
                          size="icon-sm"
                          class="text-ink-mute hover:text-ink"
                        />
                      </Message.Actions>
                    {/if}
                  {/if}
                </Message.Root>
              {/each}

              <!-- Also between steps: after a tool card comes back, the model
                   takes seconds to pick its next move and nothing else on
                   screen says the turn is still running. -->
              {#if thinking}
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

    {#if sessionExpired}
      <Callout tone="danger" title={LINK.expired}>
        Redirigiendo al inicio de sesión…
      </Callout>
    {:else if !isVoice && errorView && recovery}
      {#key chat.error}
        <ChatErrorBanner
          view={errorView}
          plan={recovery}
          onretry={retry}
          oncontinue={continueTurn}
        />
      {/key}
    {/if}

    {#if isVoice}
      <Callout
        tone="info"
        live={false}
        title="Transcripción de voz · solo lectura."
      >
        Para escribirle al robot, empezá un chat nuevo.
        {#snippet action()}
          <Button
            href={resolve("chat")}
            variant="outline"
            size="sm"
            class="gap-1.5"
          >
            <Plus class="size-3.5" aria-hidden="true" />
            Nuevo chat
          </Button>
        {/snippet}
      </Callout>
    {:else}
      <PromptInput.Root
        onSubmit={(message) => send(message.text)}
        class="panel shadow-none transition-[border-color,box-shadow] focus-within:border-accent-edge focus-within:ring-2 focus-within:ring-cyan-hot/30"
      >
        <PromptInput.Body>
          <PromptInput.Textarea
            bind:value={draft}
            bind:ref={composer}
            aria-label="Mensaje para el agente"
            placeholder="Enviá un mensaje…"
            class="text-base text-ink placeholder:text-ink-mute md:text-sm"
          />
          <PromptInput.Toolbar class="gap-3 border-t border-hairline px-3 py-2">
            <!-- While a turn runs the hint says what the stop button does NOT
                 do. It was only the button's tooltip, which a touch screen
                 never shows — and the people most likely to mistake it for
                 the robot's stop are the ones holding a phone next to it. -->
            {#if busy}
              <span id="stop-hint" class="min-w-0 readout text-ink-dim">
                Corta la respuesta y los pasos que faltan, pero no frena lo que
                el robot ya está haciendo. Para eso, Parar arriba
                (Shift+Espacio).
              </span>
            {:else}
              <span class="hidden readout sm:inline">
                Enter para enviar · Shift + Enter para salto de línea
              </span>
            {/if}
            <PromptInput.Submit
              status={chat.status}
              onStop={() => chat.stop()}
              disabled={!draft.trim()}
              aria-describedby={busy ? "stop-hint" : undefined}
              stopHint="Corta la respuesta del agente y sus próximos pasos, pero no frena lo que el robot ya está haciendo. Para eso, «Parar» en la barra superior (Shift+Espacio)."
              class="ms-auto size-9 shrink-0 rounded-full cta"
            />
          </PromptInput.Toolbar>
        </PromptInput.Body>
      </PromptInput.Root>
    {/if}
  </div>
</div>
