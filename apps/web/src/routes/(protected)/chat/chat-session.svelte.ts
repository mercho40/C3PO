/**
 * /chat's state and behaviour: the Chat instance and its lifecycle, the voice
 * transcript poll, the history rail, sending, failure recovery and the URL /
 * draft-id bookkeeping. `+page.svelte` is the markup over it.
 *
 * Moved out of the page intact, comments and all — every rule below is one
 * this page got wrong once. Construct it during component initialisation: its
 * effects belong to the page's lifetime (one of them stops a reply still
 * streaming when the page goes away), and the constructor registers the
 * `onMount` / `afterNavigate` hooks itself, between the effects, in the same
 * order the page used to — the `?q=` hand-off runs before the pin and rail
 * effects first look at the Chat.
 */

import { onMount, untrack } from "svelte";
import { afterNavigate, goto, type AfterNavigate } from "$app/navigation";
import { resolve } from "$app/paths";
import { Chat } from "@ai-sdk/svelte";
import type { UIMessage } from "ai";
import { toast } from "svelte-sonner";
import { createApi } from "#lib/api.js";
import { redirectToLogin } from "#lib/robot/poller.svelte.js";
import { agentTransport } from "#lib/chat/agent-transport.js";
import {
  awaitingModel,
  continuationPrompt,
  planRecovery,
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

export class ChatSession {
  readonly #data: () => PageData;
  /** The page's current `data` prop. Read lazily: every reader is a derived or an effect. */
  get #page(): PageData {
    return this.#data();
  }

  sessionExpired = $state(false);
  /**
   * HTTP status of the last POST /agent that failed before streaming. The SDK
   * folds every failure into `new Error(responseBody)`, which loses whether a
   * bare "Conflict" was a read-only voice chat (409) or a gateway that is down
   * (502) — and those need different advice.
   */
  failedStatus = $state<number | null>(null);

  // Talks to the backend internal agent (POST /agent); a 401 sends the
  // operator to re-auth, back to this chat afterwards — see agentTransport.
  readonly #transport = agentTransport({
    onStatus: (status) => {
      this.failedStatus =
        status !== null && (status < 200 || status > 299) ? status : null;
      if (status === 401) this.sessionExpired = true;
    },
  });

  // Id for a conversation that doesn't exist yet. Stable across re-renders, so
  // the stream and the row it persists to agree without a round-trip.
  // `newId`, not `crypto.randomUUID()`: that one only exists on https or
  // localhost, and on a phone at http://<lan-ip>:3001 this line threw during
  // hydration and took the whole page with it.
  draftId = $state(newId());

  // Two facts about the draft id, neither rendered (so neither is `$state`):
  //  - spent: a message went out under it, so the backend may now hold a row
  //    by that id and it must never name a second conversation — even when
  //    the send failed and nothing is pinned;
  //  - pinned: it is in the URL. Only after the backend accepted a turn.
  // They used to be one flag, set on the first message, which is how a send
  // to a dead backend still advertised `?id=` for a row that never existed.
  #draftSpent = false;
  #draftPinned = false;

  readonly chatId = $derived(this.#page.selected?.id ?? this.draftId);

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
  readonly chat = $derived.by(() => {
    const id = this.chatId;
    return untrack(() => {
      const selected = this.#page.selected;
      return new Chat({
        id,
        messages: (selected?.id === id
          ? selected.messages
          : []) as unknown as UIMessage[],
        transport: this.#transport,
      });
    });
  });

  // When the id does change mid-turn (back/forward, an edited URL — the rail
  // holds its links while busy), the instance being replaced is stopped (see
  // the constructor). Always the current instance, never the one being torn
  // down with the component.
  #previousChat: Chat | null = null;

  readonly busy = $derived(
    this.chat.status === "submitted" || this.chat.status === "streaming",
  );

  // ---- Voice transcripts ---------------------------------------------------

  // A voice chat is written by the realtime engine while its session runs; a
  // load-once copy is stale the moment the robot says anything else. Re-fetch
  // it while visible (see `#lib/chat/transcript.ts` for why this is safe).
  readonly isVoice = $derived(this.#page.selected?.channel === "voice");
  readonly transcript = $derived.by(() => {
    const data = this.#page;
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

  // Drives "Actualizado hace N s" only while a transcript is on screen.
  now = $state(Date.now());

  readonly freshness = $derived(
    this.transcript
      ? transcriptFreshness({
          lastOkAt: this.transcript.lastOkAt,
          failures: this.transcript.failures,
          gone: this.transcript.gone,
          now: this.now,
        })
      : null,
  );

  /** What the transcript shows: the live poll for voice, the Chat otherwise. */
  readonly messages = $derived(
    this.transcript ? this.transcript.messages : this.chat.messages,
  );

  // ---- History rail --------------------------------------------------------

  // The rail is local state seeded from the load (a writable derived: a new
  // load replaces it). Renames and deletes edit it in place instead of
  // re-running the load — see the comment on `chat` for what that used to do.
  rows = $derived(this.#page.chats ?? []);
  historyError = $derived(this.#page.historyError);
  #localTitle = $state<{ id: string; title: string } | null>(null);

  readonly currentRow = $derived(
    this.rows.find((r) => r.id === this.chatId) ?? null,
  );
  readonly title = $derived(
    (this.#localTitle?.id === this.chatId ? this.#localTitle.title : null) ??
      this.transcript?.title ??
      this.currentRow?.title ??
      this.#page.selected?.title ??
      null,
  );
  // A draft has no row until its first message reaches the backend.
  readonly renamable = $derived(
    Boolean(this.#page.selected) || this.currentRow !== null,
  );

  // The backend lists 50 by default and at most 200. "Ver más" asks for the
  // next page's worth by raising the limit; a new load starts over at one page.
  historyLimit = $derived.by(() => {
    void this.#page.chats;
    return HISTORY_PAGE;
  });
  // How many rows the last list request returned — NOT `rows.length`, which
  // a local delete shrinks (see `canLoadMoreHistory`).
  historyFetched = $derived(this.#page.chats?.length ?? 0);
  readonly canLoadMore = $derived(
    canLoadMoreHistory({
      historyError: this.historyError,
      fetched: this.historyFetched,
      limit: this.historyLimit,
    }),
  );

  // ---- Failure recovery ----------------------------------------------------

  readonly errorView = $derived(
    this.chat.error
      ? describeChatError(this.chat.error.message, this.failedStatus)
      : null,
  );
  readonly recovery = $derived(
    this.chat.error ? planRecovery(this.chat.messages) : null,
  );

  readonly thinking = $derived(
    awaitingModel(this.chat.status, this.chat.messages),
  );

  /** The `?id=` asked for couldn't be opened, and nothing has been said since. */
  readonly loadNotice = $derived.by(() => {
    const data = this.#page;
    return !data.selected && this.chat.messages.length === 0
      ? data.selectedStatus === "not-found" || data.selectedStatus === "error"
        ? data.selectedStatus
        : null
      : null;
  });

  // Whether the rail has been refreshed for the current draft. Not `$state`:
  // only the effect below reads it.
  #listRefreshed = false;
  // See `#navigated`.
  #arrived = false;

  constructor(data: () => PageData) {
    this.#data = data;

    // When the id does change mid-turn (back/forward, an edited URL — the rail
    // holds its links while busy), stop the instance being replaced rather than
    // leave it streaming into a page nobody can see. Stopping the stream does
    // not stop a bridge call already running; that is what "Parar" is for.
    $effect(() => {
      const current = this.chat;
      const previous = this.#previousChat;
      this.#previousChat = current;
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

    // And when the PAGE goes away mid-turn. The rail holds its links while busy,
    // but the console's sidebar, the topbar and the browser's Back do not — so
    // leaving /chat used to leave the agent streaming, and calling robot tools,
    // into a page nobody could see, with no Stop left to press. The instance is
    // read from `#previousChat` (always the current one, see above) rather than
    // the derived, which is being torn down with the component. This stops the
    // reply, not the robot: a bridge call already running is Parar's job, and
    // the toast says so.
    $effect(() => () => {
      const current = this.#previousChat;
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

    $effect(() => {
      const poller = this.transcript;
      if (!poller) return;
      poller.start(TRANSCRIPT_POLL_MS);
      return () => poller.stop();
    });

    $effect(() => {
      if (!this.transcript) return;
      const timer = setInterval(() => (this.now = Date.now()), 1000);
      return () => clearInterval(timer);
    });

    onMount(() => this.#mounted());
    afterNavigate((nav) => this.#navigated(nav));

    // A brand-new chat has no `?id=` until the backend has taken its first turn
    // — a reply streaming, or one already there. Pinning earlier hands out a link
    // to a chat that may never be created: with back down, the first send fails
    // and the row does not exist. Once per draft: see `shouldPinDraft` for the
    // "Nuevo chat" race that used to write the previous conversation's id back.
    $effect(() => {
      const chat = this.chat;
      const accepted =
        chat.status === "streaming" ||
        chat.messages.some((m) => m.role === "assistant");
      if (
        shouldPinDraft({
          pinned: this.#draftPinned,
          messageCount: chat.messages.length,
          hasSelected: this.#page.selected !== null,
          accepted,
        })
      ) {
        this.#rewriteUrl(true);
      }
    });

    // A conversation started from scratch is written to the database by its
    // first turn, but the rail was rendered from the server load and doesn't
    // list it. Re-read the list once the turn settles — the server's own title
    // (`titleFromParts`) rather than a guess from the prompt, since two titles
    // for one conversation is worse than a moment's delay.
    $effect(() => {
      if (
        !this.#page.selected &&
        !this.#listRefreshed &&
        !this.busy &&
        this.chat.messages.length > 0
      ) {
        this.#listRefreshed = true;
        void this.refreshHistory();
      }
    });
  }

  refreshHistory = async (limit = this.historyLimit): Promise<void> => {
    try {
      const { data: res, error } = await createApi(fetch).chats.get({
        query: limit === HISTORY_PAGE ? {} : { limit },
      });
      if (error) {
        if (error.status === 401) redirectToLogin("expired");
        else this.historyError = true;
        return;
      }
      this.rows = res?.chats ?? [];
      this.historyFetched = this.rows.length;
      this.historyLimit = limit;
      this.historyError = false;
    } catch {
      this.historyError = true;
    }
  };

  loadMoreHistory = (): Promise<void> => {
    return this.refreshHistory(
      Math.min(this.historyLimit + HISTORY_PAGE, HISTORY_MAX),
    );
  };

  renamed = (id: string, newTitle: string): void => {
    // The backend bumps `updatedAt` on rename and lists newest first; mirror
    // that so the rail doesn't reorder on the next load.
    const row = this.rows.find((r) => r.id === id);
    if (row) {
      this.rows = [
        { ...row, title: newTitle, updatedAt: new Date() },
        ...this.rows.filter((r) => r.id !== id),
      ];
    }
    if (id === this.chatId) this.#localTitle = { id, title: newTitle };
  };

  deleted = (id: string): void => {
    this.rows = this.rows.filter((r) => r.id !== id);
  };

  // ---- Composer ------------------------------------------------------------

  /**
   * Refusing must REJECT. PromptInput clears the textarea whenever onSubmit
   * resolves; returning quietly on "busy" is how an operator's next command
   * used to vanish when they pressed Enter while the agent was replying.
   * Not awaited: `sendMessage` resolves only when the whole turn ends, and the
   * composer should clear as soon as the message is on its way.
   */
  send = (text: string): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed) return Promise.reject(new Error("Mensaje vacío"));
    if (this.busy) {
      return Promise.reject(new Error("El agente todavía está respondiendo"));
    }
    if (this.isVoice) {
      return Promise.reject(new Error("Transcripción de voz: solo lectura"));
    }
    if (!this.#page.selected) this.#draftSpent = true;
    void this.chat.sendMessage({ text: trimmed });
    return Promise.resolve();
  };

  retry = (): void => {
    // `regenerate()` drops the failed reply on the client, so the server sees a
    // history ending in the user's message and mints a NEW id for the next
    // reply — while the partial one it already saved from `onEnd` stays, and a
    // reload shows both. Name the reply being replaced so the backend can
    // overwrite that row; routes/agent.ts ignores the field until it does.
    const last = this.chat.messages.at(-1);
    const replaceMessageId = last?.role === "assistant" ? last.id : undefined;
    void this.chat.regenerate(
      replaceMessageId ? { body: { replaceMessageId } } : undefined,
    );
  };

  continueTurn = (plan: Extract<RecoveryPlan, { kind: "continue" }>): void => {
    void this.chat.sendMessage({ text: continuationPrompt(plan) });
  };

  // ---- URL and draft bookkeeping ---------------------------------------------

  /**
   * Rewrite the query without navigating: always drop `q` — it's a one-shot
   * hand-off; resending it on reload would silently re-issue a robot command —
   * and, with `pin`, add `?id=` so a reload resumes this chat.
   */
  #rewriteUrl(pin: boolean): void {
    const params = new URLSearchParams(window.location.search);
    params.delete("q");
    if (pin) {
      this.#draftPinned = true;
      params.set("id", this.chatId);
    }
    const query = params.toString();
    const url = resolve(query ? `chat?${query}` : "chat");
    void goto(url, { shallow: true, replace: true });
  }

  /**
   * `onMount`. Hand-off from the dashboard command box / quick controls: an
   * initial prompt arrives as `?q=…`. Send it once, then strip it so a reload
   * doesn't resend it. The id is pinned by the effect in the constructor once
   * the backend has accepted the turn — not here, before anyone knows whether
   * it will.
   *
   * The rewrite waits a task: on a direct load this runs during hydration,
   * before SvelteKit's router has started, and a shallow `goto` throws there.
   */
  #mounted(): (() => void) | undefined {
    const q = new URLSearchParams(window.location.search).get("q")?.trim();
    if (q && !this.isVoice) {
      this.#draftSpent = true;
      void this.chat.sendMessage({ text: q });
      const timer = setTimeout(() => this.#rewriteUrl(false));
      return () => clearTimeout(timer);
    }
  }

  /**
   * `afterNavigate`. "Nuevo chat" is a link to /chat, and the router answers it
   * by re-running the load against this same component instance —
   * `data.selected` goes null but the instance, and its draft id, survive.
   * Reusing a spent id would make the backend append the new conversation onto
   * the old one's row: two unrelated transcripts in a single chat, visible the
   * moment either is reloaded. Mint a fresh one instead.
   *
   * Except on the FIRST call, which is the navigation that mounted this page:
   * the draft is brand new — or a `?q=` hand-off spent it a moment ago, on
   * purpose, in `#mounted`, which runs before this. Minting there threw the
   * dashboard's command away into a Chat nobody could see.
   *
   * And never on `#rewriteUrl`'s own shallow `goto`: SvelteKit 3 reports those
   * to afterNavigate too, and the `?q=` hand-off spends the draft right before
   * rewriting, so minting there would throw its command away the same way.
   */
  #navigated({ shallow }: AfterNavigate): void {
    if (shallow) return;
    if (!this.#arrived) {
      this.#arrived = true;
      return;
    }
    if (!this.#page.selected && this.#draftSpent) {
      this.draftId = newId();
      this.#draftSpent = false;
      this.#draftPinned = false;
      this.#listRefreshed = false;
    }
  }
}
