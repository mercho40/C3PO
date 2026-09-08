<script lang="ts">
  // Conversation list for /chat.
  //
  // These are plain links, not client-side state: each is a real URL an
  // operator can bookmark or paste into an incident report, and the server load
  // already has the messages ready on first paint.
  import { goto, invalidateAll } from "$app/navigation";
  import { MessageSquare, Mic, Plus, Trash2 } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { createApi } from "$lib/api";

  type ChatSummary = {
    id: string;
    title: string | null;
    channel: "text" | "voice";
    updatedAt: string | Date;
  };

  let {
    chats,
    activeId,
    onnavigate,
  }: {
    chats: ChatSummary[];
    activeId: string;
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

  // Open state is its own bound boolean rather than being derived from
  // `pendingDelete`: AlertDialog.Root takes `open` as a `$bindable`, and a
  // one-way `open={…}` prop does not drive it.
  let pendingDelete = $state<ChatSummary | null>(null);
  let deleteOpen = $state(false);
  let deleting = $state(false);

  function askDelete(item: ChatSummary) {
    pendingDelete = item;
    deleteOpen = true;
  }

  async function confirmDelete() {
    const target = pendingDelete;
    if (!target || deleting) return;
    deleting = true;
    try {
      await createApi(fetch).chats({ id: target.id }).delete();
      // Deleting the conversation you're reading would leave the page showing
      // messages that no longer exist — start a fresh one instead.
      if (target.id === activeId) await goto("/chat", { invalidateAll: true });
      else await invalidateAll();
    } finally {
      deleting = false;
      deleteOpen = false;
      pendingDelete = null;
    }
  }
</script>

<div class="flex min-h-0 flex-col gap-3">
  <Button
    href="/chat"
    variant="outline"
    onclick={() => onnavigate?.()}
    class="w-full justify-start gap-2 tile-interactive text-ink"
  >
    <Plus class="size-4" />
    Nuevo chat
  </Button>

  <div class="-mr-1 min-h-0 flex-1 overflow-y-auto pr-1">
    {#if groups.length === 0}
      <p class="px-1 py-6 text-center text-xs text-ink-mute">
        Todavía no hay conversaciones.
      </p>
    {:else}
      <div class="flex flex-col gap-4">
        {#each groups as group (group.label)}
          <div class="flex flex-col gap-1">
            <span class="px-1 pb-1 eyebrow">{group.label}</span>
            {#each group.items as item (item.id)}
              {@const active = item.id === activeId}
              <div class="group/row relative">
                <a
                  href={`/chat?id=${item.id}`}
                  onclick={() => onnavigate?.()}
                  title={item.title ?? "Sin título"}
                  aria-current={active ? "page" : undefined}
                  class="flex items-center gap-2 rounded-md py-2 pr-9 pl-2.5 text-sm transition-colors {active
                    ? 'bg-accent text-ink'
                    : 'text-ink-mute hover:bg-wash-hover hover:text-ink'}"
                >
                  {#if item.channel === "voice"}
                    <Mic class="size-3.5 shrink-0" aria-label="Conversación de voz" />
                  {:else}
                    <MessageSquare class="size-3.5 shrink-0" />
                  {/if}
                  <span class="truncate">{item.title ?? "Sin título"}</span>
                </a>
                <!-- Revealed on hover/focus so the list stays calm, but always
                     reachable by keyboard. -->
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Eliminar {item.title ?? 'conversación'}"
                  onclick={() => askDelete(item)}
                  class="absolute top-1/2 right-1 -translate-y-1/2 text-ink-mute opacity-0 transition-opacity group-hover/row:opacity-100 hover:text-danger-soft focus-visible:opacity-100"
                >
                  <Trash2 class="size-3.5" />
                </Button>
              </div>
            {/each}
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<AlertDialog.Root bind:open={deleteOpen}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>¿Eliminar la conversación?</AlertDialog.Title>
      <AlertDialog.Description>
        Se borrarán «{pendingDelete?.title ?? "Sin título"}» y todos sus
        mensajes. Esta acción no se puede deshacer.
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel disabled={deleting}>Cancelar</AlertDialog.Cancel>
      <AlertDialog.Action
        variant="destructive"
        disabled={deleting}
        onclick={confirmDelete}
      >
        <Trash2 />
        Eliminar
      </AlertDialog.Action>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
