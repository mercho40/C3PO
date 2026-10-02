<!--
  Past voice sessions, as saved transcripts — /voice's history column.

  A voice transcript is a chat with `channel: "voice"`, read-only in /chat (back
  refuses typed turns into it). Loaded server-side; a failed load says so
  rather than rendering as "no transcripts yet", because those are different
  facts and only one of them means there is nothing to look at.

  The list is the voice chats among your last N conversations (GET /chats has
  no channel filter), and only the realtime engine saves transcripts — both
  worded by `transcriptsCopy`, so the empty state never claims more than was
  looked at and never describes the Local engine as one that saves.
-->
<script lang="ts">
  import { ChevronRight } from "@lucide/svelte";
  import { resolve } from "$app/paths";
  import Callout from "$lib/components/callout.svelte";
  import { transcriptsCopy, type Engine } from "./voice-view";

  type Transcript = {
    id: string;
    title: string | null;
    createdAt: string;
    updatedAt: string;
  };

  let {
    transcripts,
    error,
    capped = false,
    scanned,
    engine,
  }: {
    transcripts: Transcript[] | null;
    error: string | null;
    /** The scan came back full: older voice chats may exist unseen. */
    capped?: boolean;
    /** How many recent chats were scanned. */
    scanned: number;
    /** The voice engine in use now, or null when its status is unknown. */
    engine: Engine | null;
  } = $props();

  const copy = $derived(
    transcriptsCopy({
      engine,
      count: transcripts?.length ?? 0,
      capped,
      scanned,
    }),
  );

  const when = new Intl.DateTimeFormat("es-AR", {
    dateStyle: "medium",
    timeStyle: "short",
  });
</script>

<section
  class="flex min-w-0 flex-col gap-3 panel p-5"
  aria-labelledby="voice-history-heading"
>
  <div class="flex items-baseline justify-between gap-3">
    <h2 id="voice-history-heading" class="eyebrow">
      Transcripciones anteriores
    </h2>
    {#if transcripts?.length}
      <span class="readout">{transcripts.length}</span>
    {/if}
  </div>

  {#if error}
    <Callout tone="warn" title="No se pudo leer el historial">{error}</Callout>
  {:else if !transcripts || transcripts.length === 0}
    <p class="text-sm text-ink-mute">{copy.empty} {copy.engine}</p>
  {:else}
    {#if copy.scope}
      <p class="readout">{copy.scope}</p>
    {/if}
    <ul class="flex flex-col gap-1.5">
      {#each transcripts as t (t.id)}
        <li>
          <a
            href={resolve(`/chat?id=${encodeURIComponent(t.id)}`)}
            class="group flex items-center gap-3 tile-interactive px-3 py-2.5 focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
          >
            <span class="flex min-w-0 flex-1 flex-col gap-0.5">
              <span class="truncate text-sm text-ink">
                {t.title ?? "Conversación de voz"}
              </span>
              <span class="readout">
                <time datetime={t.createdAt}
                  >{when.format(new Date(t.createdAt))}</time
                >
                {#if t.updatedAt !== t.createdAt}
                  · última actividad
                  <time datetime={t.updatedAt}
                    >{when.format(new Date(t.updatedAt))}</time
                  >
                {/if}
              </span>
            </span>
            <ChevronRight
              class="size-4 shrink-0 text-ink-mute transition-colors group-hover:text-ink"
              aria-hidden="true"
            />
          </a>
        </li>
      {/each}
    </ul>
    <p class="text-xs text-ink-mute">{copy.engine}</p>
  {/if}
</section>
