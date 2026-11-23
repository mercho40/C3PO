<script lang="ts">
  /**
   * /chat — the agent conversation, its history rail and, for a voice chat,
   * the live transcript. The state and behaviour are `ChatSession`
   * (`./chat-session.svelte.ts`); this file is the markup over it, plus the
   * composer's own UI state.
   */
  import { tick } from "svelte";
  import { refreshAll } from "$app/navigation";
  import { isToolUIPart } from "ai";
  import { resolve } from "$app/paths";
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
  import { LINK } from "#lib/robot/link-copy.js";
  import { replyText } from "#lib/chat/turn.js";
  import { ChatSession } from "./chat-session.svelte";
  import type { PageData } from "./$types";

  let { data }: { data: PageData } = $props();

  const session = new ChatSession(() => data);

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
</script>

<div class="flex h-full min-h-0 gap-4">
  <!-- Conversation rail. Hidden below lg, where it becomes a sheet. -->
  <aside
    aria-label="Conversaciones"
    class="hidden w-64 shrink-0 flex-col panel p-3 lg:flex"
  >
    <ChatHistory
      chats={session.rows}
      activeId={session.chatId}
      busy={session.busy}
      historyError={session.historyError}
      canLoadMore={session.canLoadMore}
      onretry={() => session.refreshHistory()}
      onloadmore={session.loadMoreHistory}
      ondeleted={session.deleted}
      onrenamed={session.renamed}
    />
  </aside>

  <Sheet.Root bind:open={historyOpen}>
    <Sheet.Content side="left" class="w-[280px] p-3">
      <Sheet.Header class="px-1 pb-2">
        <Sheet.Title class="text-sm">Conversaciones</Sheet.Title>
      </Sheet.Header>
      <ChatHistory
        chats={session.rows}
        activeId={session.chatId}
        busy={session.busy}
        historyError={session.historyError}
        canLoadMore={session.canLoadMore}
        onretry={() => session.refreshHistory()}
        onloadmore={session.loadMoreHistory}
        ondeleted={session.deleted}
        onrenamed={session.renamed}
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
        {#key session.chatId}
          <ChatTitle
            id={session.chatId}
            title={session.title}
            renamable={session.renamable}
            onrenamed={session.renamed}
          />
        {/key}
      </div>
      {#if session.freshness}
        <span
          class={[
            "max-w-full min-w-0 truncate readout",
            session.freshness.tone === "warn" && "text-warn",
          ]}
        >
          {session.freshness.text}
        </span>
      {/if}
      <!-- The announcement is its own region, present for as long as a
           transcript is open, and says only what changed STATE — the visible
           line ticks every second and used to carry the live role itself. -->
      {#if session.transcript}
        <span role="status" class="sr-only">
          {session.freshness?.announce ?? ""}
        </span>
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
              session.messages.length === 0 && "min-h-full justify-center",
            ]}
          >
            {#if session.loadNotice === "not-found"}
              <Callout tone="warn" title="No encontramos esa conversación">
                Puede que la hayan eliminado o que sea de otra cuenta. Podés
                empezar una nueva acá.
              </Callout>
            {:else if session.loadNotice === "error"}
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

            {#if session.messages.length === 0}
              {#if session.isVoice}
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
                      class="text-ink-dim"
                    >
                      Parar
                    </strong>
                    en la barra superior (<kbd
                      class="rounded border border-hairline-strong px-1 font-mono text-2xs"
                    >
                      Shift
                    </kbd>
                    +
                    <kbd
                      class="rounded border border-hairline-strong px-1 font-mono text-2xs"
                    >
                      Espacio
                    </kbd>), no el chat.
                  </p>
                </div>
              {/if}
            {:else}
              {#each session.messages as message, mi (message.id)}
                {@const live =
                  session.busy && mi === session.messages.length - 1}
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
                        <ToolCard
                          {part}
                          {live}
                          onconfirmed={(message: string) =>
                            // Confirmar already ran it; the agent reads the
                            // outcome and continues. A voice transcript is
                            // read-only: back tells its realtime session.
                            session.isVoice
                              ? toast.info(
                                  "Se ejecutó. Si la sesión de voz sigue activa, el agente ya lo sabe.",
                                )
                              : session
                                  .send(message)
                                  .catch((e: Error) => toast.error(e.message))}
                        />
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
              {#if session.thinking}
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

    {#if session.sessionExpired}
      <Callout tone="danger" title={LINK.expired}>
        Redirigiendo al inicio de sesión…
      </Callout>
    {:else if !session.isVoice && session.errorView && session.recovery}
      {#key session.chat.error}
        <ChatErrorBanner
          view={session.errorView}
          plan={session.recovery}
          onretry={session.retry}
          oncontinue={session.continueTurn}
        />
      {/key}
    {/if}

    {#if session.isVoice}
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
        onSubmit={(message) => session.send(message.text)}
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
            {#if session.busy}
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
              status={session.chat.status}
              onStop={() => session.chat.stop()}
              disabled={!draft.trim()}
              aria-describedby={session.busy ? "stop-hint" : undefined}
              stopHint="Corta la respuesta del agente y sus próximos pasos, pero no frena lo que el robot ya está haciendo. Para eso, «Parar» en la barra superior (Shift+Espacio)."
              class="ms-auto size-9 shrink-0 rounded-full cta"
            />
          </PromptInput.Toolbar>
        </PromptInput.Body>
      </PromptInput.Root>
    {/if}
  </div>
</div>
