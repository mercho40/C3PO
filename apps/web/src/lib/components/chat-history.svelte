<script lang="ts">
  // Conversation list for /chat.
  //
  // These are plain links, not client-side state: each is a real URL an
  // operator can bookmark or paste into an incident report, and the server load
  // already has the messages ready on first paint.
  //
  // The list itself is owned by the page (it is rendered twice — the rail and
  // the mobile sheet — and both must agree). This component asks the backend
  // to rename or delete, and reports the outcome upward; it never re-runs the
  // page load. It used to `invalidateAll()` after a delete, which rebuilt the
  // page's `Chat` from the database copy mid-turn: the reply being streamed
  // vanished from view, the old instance kept streaming (and the robot kept
  // executing) in the background, and Stop could no longer reach it.
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { tick } from "svelte";
  import {
    Ellipsis,
    MessageSquare,
    Mic,
    Pencil,
    Plus,
    RotateCw,
    Trash2,
  } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu/index.js";
  import { createApi } from "$lib/api";
  import { redirectToLogin } from "$lib/robot/poller.svelte";
  import { normalizeTitle, TITLE_MAX, titleOf } from "$lib/chat/title";

  type ChatSummary = {
    id: string;
    title: string | null;
    channel: "text" | "voice";
    updatedAt: string | Date;
  };

  let {
    chats,
    activeId,
    busy = false,
    historyError = false,
    canLoadMore = false,
    onretry,
    onloadmore,
    ondeleted,
    onrenamed,
    onnavigate,
  }: {
    chats: ChatSummary[];
    activeId: string;
    /** A reply is streaming into the open chat. */
    busy?: boolean;
    /** The list request failed — the rail is not empty, it is unknown. */
    historyError?: boolean;
    onretry?: () => void | Promise<void>;
    /** There may be older conversations past the current page. */
    canLoadMore?: boolean;
    onloadmore?: () => Promise<void>;
    /** The backend confirmed the delete (or the chat was already gone). */
    ondeleted: (id: string) => void;
    onrenamed: (id: string, title: string) => void;
    /** Lets the mobile sheet close itself when a conversation is picked. */
    onnavigate?: () => void;
  } = $props();

  // Buckets instead of a flat list: an operator looking for "the run where it
  // fell over" is reaching for a day, not a position in a list.
  const groups = $derived.by(() => {
    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);
    const dayMs = 86_400_000;

    const buckets: { label: string; items: ChatSummary[] }[] = [
      { label: "Hoy", items: [] },
      { label: "Ayer", items: [] },
      { label: "Últimos 7 días", items: [] },
      { label: "Anteriores", items: [] },
    ];

    for (const c of chats) {
      const ts = new Date(c.updatedAt).getTime();
      if (ts >= startOfToday.getTime()) buckets[0].items.push(c);
      else if (ts >= startOfToday.getTime() - dayMs) buckets[1].items.push(c);
      else if (ts >= startOfToday.getTime() - 7 * dayMs)
        buckets[2].items.push(c);
      else buckets[3].items.push(c);
    }

    return buckets.filter((b) => b.items.length > 0);
  });

  // While a reply streams, leaving the open chat would orphan it: the turn
  // keeps running on the server, but the page that could show it and stop it
  // is gone. So switching is held until the turn ends (or is stopped), and the
  // rail says so instead of a link that silently does nothing.
  const HOLD_MESSAGE =
    "Esperá a que termine la respuesta (o detenela) antes de cambiar de conversación.";

  function guardNavigation(e: MouseEvent, targetId: string | null) {
    if (busy && targetId !== activeId) {
      e.preventDefault();
      toast(HOLD_MESSAGE);
      return;
    }
    onnavigate?.();
  }

  let loadingMore = $state(false);
  async function loadMore() {
    if (loadingMore) return;
    loadingMore = true;
    try {
      await onloadmore?.();
    } finally {
      loadingMore = false;
    }
  }

  let retrying = $state(false);
  async function retry() {
    if (retrying) return;
    retrying = true;
    try {
      await onretry?.();
    } finally {
      retrying = false;
    }
  }

  // ---- Rename ------------------------------------------------------------

  let renamingId = $state<string | null>(null);
  let renameValue = $state("");
  let renameSaving = $state(false);
  let renameInput = $state<HTMLInputElement | null>(null);

  async function startRename(item: ChatSummary) {
    renamingId = item.id;
    renameValue = item.title ?? "";
    await tick();
    renameInput?.select();
  }

  async function saveRename(item: ChatSummary) {
    if (renameSaving) return;
    const next = normalizeTitle(renameValue);
    if (next === null || next === (item.title ?? "")) {
      renamingId = null;
      return;
    }
    renameSaving = true;
    try {
      const { data, error } = await createApi(fetch)
        .chats({ id: item.id })
        .patch({ title: next });
      if (error) {
        if (error.status === 401) {
          redirectToLogin("expired");
          return;
        }
        toast.error(
          error.status === 404
            ? "Esa conversación ya no existe."
            : "No se pudo renombrar la conversación.",
        );
        return;
      }
      onrenamed(item.id, data?.title ?? next);
      renamingId = null;
    } catch {
      toast.error("No se pudo renombrar: el servidor no respondió.");
    } finally {
      renameSaving = false;
    }
  }

  function renameKeydown(e: KeyboardEvent, item: ChatSummary) {
    if (e.key === "Enter") {
      e.preventDefault();
      void saveRename(item);
    } else if (e.key === "Escape") {
      e.preventDefault();
      renamingId = null;
    }
  }

  // ---- Delete ------------------------------------------------------------

  // Open state is its own bound boolean rather than being derived from
  // `pendingDelete`: AlertDialog.Root takes `open` as a `$bindable`, and a
  // one-way `open={…}` prop does not drive it.
  let pendingDelete = $state<ChatSummary | null>(null);
  let deleteOpen = $state(false);
  let deleting = $state(false);
  let deleteError = $state<string | null>(null);

  function askDelete(item: ChatSummary) {
    pendingDelete = item;
    deleteError = null;
    deleteOpen = true;
  }

  async function confirmDelete() {
    const target = pendingDelete;
    if (!target || deleting) return;
    deleting = true;
    deleteError = null;
    try {
      const { error } = await createApi(fetch)
        .chats({ id: target.id })
        .delete();
      if (error && error.status === 401) {
        deleteOpen = false;
        redirectToLogin("expired");
        return;
      }
      // A 404 means it is already gone (deleted from another tab, or by
      // another session) — the state the operator asked for. Anything else
      // is a failure, and the dialog stays open saying so: closing it would
      // read as success over a conversation that still exists.
      if (error && error.status !== 404) {
        deleteError = "No se pudo eliminar la conversación. Probá de nuevo.";
        return;
      }
      if (error) toast("Esa conversación ya no existía.");
      deleteOpen = false;
      pendingDelete = null;
      ondeleted(target.id);
      // Deleting the conversation you're reading would leave the page showing
      // messages that no longer exist — start a fresh one instead.
      if (target.id === activeId) await goto(resolve("/chat"));
    } catch {
      deleteError =
        "No se pudo eliminar la conversación: el servidor no respondió.";
    } finally {
      deleting = false;
    }
  }
</script>

<div class="flex min-h-0 flex-col gap-3">
  <Button
    href={resolve("/chat")}
    variant="outline"
    aria-disabled={busy ? "true" : undefined}
    onclick={(e: MouseEvent) => guardNavigation(e, null)}
    class={[
      "w-full justify-start gap-2 tile-interactive text-ink",
      busy && "opacity-60",
    ]}
  >
    <Plus class="size-4" aria-hidden="true" />
    Nuevo chat
  </Button>

  {#if busy}
    <p role="status" class="px-1 text-xs text-ink-mute">{HOLD_MESSAGE}</p>
  {/if}

  <div class="-mr-1 min-h-0 flex-1 overflow-y-auto pr-1">
    {#if historyError}
      <div
        role="alert"
        class="flex flex-col items-center gap-2 px-1 py-6 text-center"
      >
        <p class="text-xs text-danger-soft">No se pudo cargar el historial.</p>
        {#if onretry}
          <Button
            variant="outline"
            size="sm"
            class="gap-1.5"
            disabled={retrying}
            onclick={retry}
          >
            <RotateCw
              class={["size-3.5", retrying && "animate-spin"]}
              aria-hidden="true"
            />
            Reintentar
          </Button>
        {/if}
      </div>
    {/if}
    {#if groups.length === 0 && !historyError}
      <p class="px-1 py-6 text-center text-xs text-ink-mute">
        Todavía no hay conversaciones.
      </p>
    {:else if groups.length > 0}
      <div class="flex flex-col gap-4">
        {#each groups as group (group.label)}
          <div class="flex flex-col gap-1">
            <h2 class="px-1 pb-1 eyebrow">{group.label}</h2>
            {#each group.items as item (item.id)}
              {@const active = item.id === activeId}
              {@const held = busy && !active}
              <div class="group/row relative">
                {#if renamingId === item.id}
                  <input
                    bind:this={renameInput}
                    bind:value={renameValue}
                    onkeydown={(e) => renameKeydown(e, item)}
                    onblur={() => {
                      // Escape already closed it; a late blur must not save.
                      if (renamingId === item.id) void saveRename(item);
                    }}
                    maxlength={TITLE_MAX}
                    disabled={renameSaving}
                    aria-label="Nuevo título para «{titleOf(item)}»"
                    class="h-9 w-full rounded-md border border-hairline-strong bg-trench px-2.5 text-base text-ink outline-none focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 md:text-sm"
                  />
                {:else}
                  <a
                    href={resolve(`/chat?id=${encodeURIComponent(item.id)}`)}
                    onclick={(e) => guardNavigation(e, item.id)}
                    title={titleOf(item)}
                    aria-current={active ? "page" : undefined}
                    aria-disabled={held ? "true" : undefined}
                    class={[
                      "flex items-center gap-2 rounded-md py-2 pr-9 pl-2.5 text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-cyan-hot/50",
                      active
                        ? "bg-accent text-ink"
                        : "text-ink-mute hover:bg-wash-hover hover:text-ink",
                      held && "opacity-60",
                    ]}
                  >
                    {#if item.channel === "voice"}
                      <Mic class="size-3.5 shrink-0" aria-hidden="true" />
                      <span class="sr-only">Conversación de voz:</span>
                    {:else}
                      <MessageSquare
                        class="size-3.5 shrink-0"
                        aria-hidden="true"
                      />
                    {/if}
                    <span class="truncate">{titleOf(item)}</span>
                  </a>
                  <!-- Revealed on hover/focus on a desktop so the list stays
                       calm, always visible where there is no hover (phones,
                       tablets, the Quest browser) — tapping an invisible
                       button was the only way to find it there. -->
                  <DropdownMenu.Root>
                    <DropdownMenu.Trigger>
                      {#snippet child({ props })}
                        <Button
                          {...props}
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Acciones para «{titleOf(item)}»"
                          class="absolute top-1/2 right-1 -translate-y-1/2 text-ink-mute opacity-0 transition-opacity group-hover/row:opacity-100 hover:text-ink focus-visible:opacity-100 data-[state=open]:opacity-100 pointer-coarse:opacity-100 [@media(hover:none)]:opacity-100"
                        >
                          <Ellipsis class="size-3.5" aria-hidden="true" />
                        </Button>
                      {/snippet}
                    </DropdownMenu.Trigger>
                    <!-- Don't hand focus back to the trigger after "Renombrar":
                         the trigger is being replaced by the title input, and
                         the restore would blur (and so close) the input the
                         moment it opened. -->
                    <DropdownMenu.Content
                      align="end"
                      class="min-w-40"
                      onCloseAutoFocus={(e) => {
                        if (renamingId) e.preventDefault();
                      }}
                    >
                      <DropdownMenu.Item
                        onSelect={() => void startRename(item)}
                      >
                        <Pencil aria-hidden="true" />
                        Renombrar
                      </DropdownMenu.Item>
                      <DropdownMenu.Item
                        variant="destructive"
                        disabled={busy && active}
                        onSelect={() => askDelete(item)}
                      >
                        <Trash2 aria-hidden="true" />
                        {busy && active
                          ? "Eliminar (esperá la respuesta)"
                          : "Eliminar"}
                      </DropdownMenu.Item>
                    </DropdownMenu.Content>
                  </DropdownMenu.Root>
                {/if}
              </div>
            {/each}
          </div>
        {/each}
        {#if canLoadMore && onloadmore}
          <Button
            variant="ghost"
            size="sm"
            class="w-full text-ink-mute hover:text-ink"
            disabled={loadingMore}
            onclick={loadMore}
          >
            {loadingMore ? "Cargando…" : "Ver más"}
          </Button>
        {/if}
      </div>
    {/if}
  </div>
</div>

<AlertDialog.Root bind:open={deleteOpen}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>¿Eliminar la conversación?</AlertDialog.Title>
      <AlertDialog.Description>
        Se borrarán «{titleOf(pendingDelete)}» y todos sus mensajes. Esta acción
        no se puede deshacer.
      </AlertDialog.Description>
      {#if deleteError}
        <p role="alert" class="text-sm text-danger-soft">{deleteError}</p>
      {/if}
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel disabled={deleting}>Cancelar</AlertDialog.Cancel>
      <AlertDialog.Action
        variant="destructive"
        disabled={deleting}
        onclick={confirmDelete}
      >
        <Trash2 aria-hidden="true" />
        {deleting ? "Eliminando…" : "Eliminar"}
      </AlertDialog.Action>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
