<script lang="ts">
  /*
   * The open conversation's title, with inline rename.
   *
   * The topbar always says "Chat" and owns the tab title, so this is the only
   * place an operator can tell which conversation they are reading — and,
   * since titles are derived from the first prompt and cut at 80 characters,
   * the only way to make "Caminá 2 metros hacia adelante y después…" into
   * "Prueba de marcha · pasillo B" before pasting the link in an incident note.
   *
   * `stamp-quiet`, not `stamp`: the display face at full weight is rationed to
   * the robot's name, its posture and the page title.
   */
  import { tick } from "svelte";
  import { Check, Pencil, X } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { Button } from "#lib/components/ui/button/index.js";
  import { createApi } from "#lib/api.js";
  import { redirectToLogin } from "#lib/robot/poller.svelte.js";
  import { normalizeTitle, TITLE_MAX, titleOf } from "#lib/chat/title.js";

  let {
    id,
    title,
    renamable,
    onrenamed,
  }: {
    id: string;
    title: string | null;
    /** False until the backend knows this chat (a draft has no row yet). */
    renamable: boolean;
    onrenamed: (id: string, title: string) => void;
  } = $props();

  let editing = $state(false);
  let saving = $state(false);
  let value = $state("");
  let input = $state<HTMLInputElement | null>(null);

  async function startEditing() {
    value = title ?? "";
    editing = true;
    await tick();
    input?.select();
  }

  function cancel() {
    editing = false;
  }

  async function save() {
    if (saving) return;
    const next = normalizeTitle(value);
    // Unchanged or emptied: nothing to send. An empty title would 422, and
    // "no change" is not worth a request.
    if (next === null || next === (title ?? "")) {
      editing = false;
      return;
    }
    saving = true;
    try {
      const { data, error } = await createApi(fetch)
        .chats({ id })
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
      onrenamed(id, data?.title ?? next);
      editing = false;
    } catch {
      toast.error("No se pudo renombrar: el servidor no respondió.");
    } finally {
      saving = false;
    }
  }

  function onkeydown(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      void save();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  }
</script>

{#if editing}
  <form
    class="flex min-w-0 flex-1 items-center gap-1.5"
    onsubmit={(e) => {
      e.preventDefault();
      void save();
    }}
  >
    <input
      bind:this={input}
      bind:value
      {onkeydown}
      maxlength={TITLE_MAX}
      disabled={saving}
      aria-label="Título de la conversación"
      class="h-8 min-w-0 flex-1 rounded-md border border-hairline-strong bg-trench px-2 text-base text-ink outline-none focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 md:text-sm"
    />
    <Button
      type="submit"
      variant="ghost"
      size="icon-sm"
      disabled={saving}
      aria-label="Guardar título"
      class="text-ink-mute hover:text-ink"
    >
      <Check class="size-4" aria-hidden="true" />
    </Button>
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      disabled={saving}
      aria-label="Cancelar"
      class="text-ink-mute hover:text-ink"
      onclick={cancel}
    >
      <X class="size-4" aria-hidden="true" />
    </Button>
  </form>
{:else}
  <div class="flex min-w-0 flex-1 items-center gap-1">
    <h2
      class="truncate stamp-quiet text-base {title || !renamable
        ? 'text-ink'
        : 'text-ink-mute'}"
      title={renamable ? titleOf({ title }) : undefined}
    >
      {renamable ? titleOf({ title }) : "Nuevo chat"}
    </h2>
    {#if renamable}
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Renombrar conversación"
        title="Renombrar"
        class="shrink-0 text-ink-mute hover:text-ink"
        onclick={startEditing}
      >
        <Pencil class="size-3.5" aria-hidden="true" />
      </Button>
    {/if}
  </div>
{/if}
