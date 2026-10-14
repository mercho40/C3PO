<!--
  "Registro de comandos" — the command log (GET /audit), admins only.

  Every skill dispatched to the robot, by any path: the agent from a typed
  chat, a voice session, or directly (VR buttons, the walk loop, bring-up,
  PARAR). Newest first, paged by cursor ("Cargar más") because the log grows
  while you read it — an offset would shift under you and repeat rows.

  Outcomes go through the console's one classifier (`rowOutcome` →
  `skill-outcome.ts`): a row back recorded as `ok` can still be a stub that only
  logged, and shows as such here exactly as it would in /chat or /vr-control.

  Read on demand, not polled: an audit view that rewrites itself under the
  admin while they are expanding a row is worse than a "Actualizar" button.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { ChevronDown, RefreshCw } from "@lucide/svelte";
  import { page } from "$app/state";
  import { resolve } from "$app/paths";
  import Callout from "#lib/components/callout.svelte";
  import { createApi } from "#lib/api.js";
  import { redirectToLogin } from "#lib/robot/poller.svelte.js";
  import { formatSince } from "#lib/robot/format.js";
  import { OUTCOME_TONE } from "#lib/robot/skill-outcome.js";
  import {
    auditErrorText,
    auditQuery,
    exactTime,
    formatDuration,
    rowOutcome,
    sourceLabel,
    whoLabel,
    type AuditError,
    type AuditFilters,
    type AuditRow,
  } from "#lib/admin/audit.js";

  let { now }: { now: number } = $props();

  const PAGE = 50;

  let rows = $state.raw<AuditRow[]>([]);
  let nextCursor = $state<string | null>(null);
  let loading = $state(false);
  let loaded = $state(false);
  let error = $state.raw<AuditError | null>(null);
  let filters = $state<AuditFilters>({ skill: "", status: "", source: "" });
  let skillInput = $state("");
  let expanded = $state<Record<string, boolean>>({});

  const myId = $derived(page.data.user?.id ?? null);

  const json = (v: unknown) => JSON.stringify(v, null, 2);

  // The request in flight, a generation counter, and which filters the rows on
  // screen answer (as a comparable key). Not reactive: nothing on screen reads
  // them.
  let inFlight: AbortController | null = null;
  let generation = 0;
  let shownFor: string | null = null;
  /** A new filter is loading; the rows on screen answer the previous one. */
  let superseded = $state(false);

  const keyOf = (f: AuditFilters) => JSON.stringify(auditQuery(f, null, PAGE));

  /**
   * Read one page. `reset` starts over with the CURRENT filters.
   *
   * A filter change SUPERSEDES whatever is in flight: it aborts it and asks
   * again. The old guard (`if (loading) return`) dropped the new request
   * instead, while the selects stayed enabled — so changing "Resultado" during
   * a slow read left a list that did not match the filter on screen, and a
   * "Cargar más" in flight could append rows from the previous filter under the
   * new one. The generation check is the second half: an aborted read can
   * still resolve (Eden turns the abort into an `{error}` rather than a throw),
   * and only the latest request may touch the list.
   *
   * "Cargar más" never supersedes anything: it is only offered once a page has
   * settled, and a second click while one is loading is a no-op.
   */
  async function load(reset: boolean) {
    if (!reset && loading) return;
    inFlight?.abort();
    const controller = new AbortController();
    inFlight = controller;
    const mine = ++generation;
    const key = keyOf(filters);
    // A timer rather than `AbortSignal.any([…, AbortSignal.timeout()])`, which
    // older Safari (and so some iPads) does not have.
    const timer = setTimeout(
      () => controller.abort(new DOMException("timeout", "TimeoutError")),
      8000,
    );
    loading = true;
    superseded = reset && shownFor !== null && shownFor !== key;
    error = null;
    try {
      const { data, error: err } = await createApi(fetch).audit.get({
        query: auditQuery(filters, reset ? null : nextCursor, PAGE),
        fetch: { signal: controller.signal },
      });
      if (mine !== generation) return;
      if (err) {
        // An abort surfaces as Eden's network shape; the abort REASON says
        // whether it was the timer above.
        const value = controller.signal.aborted
          ? controller.signal.reason
          : err.value;
        const failure = auditErrorText(err.status, value);
        if (failure.kind === "expired") {
          redirectToLogin("expired");
          return;
        }
        error = failure;
        dropIfStale(key);
        return;
      }
      if (!data) return;
      rows = reset ? data.rows : [...rows, ...data.rows];
      nextCursor = data.nextCursor;
      shownFor = key;
      loaded = true;
    } catch {
      // Eden does not throw for network failures (see above); what lands here
      // is a response it could not decode — back answered, but wrongly.
      if (mine === generation) {
        error = auditErrorText(null, null);
        dropIfStale(key);
      }
    } finally {
      clearTimeout(timer);
      if (mine === generation) {
        loading = false;
        superseded = false;
        inFlight = null;
      }
    }
  }

  /**
   * A failed read under NEW filters must not leave the old filter's rows on
   * screen beneath the new selects — that is the mismatch this component used
   * to produce. Under the same filters ("Actualizar" failed) the rows are still
   * a true answer to the question on screen, just not a fresh one; they stay.
   */
  function dropIfStale(key: string) {
    if (shownFor === null || shownFor === key) return;
    rows = [];
    nextCursor = null;
    shownFor = null;
    loaded = false;
  }

  function applyFilters(event?: SubmitEvent) {
    event?.preventDefault();
    filters.skill = skillInput;
    expanded = {};
    void load(true);
  }

  onMount(() => {
    void load(true);
    // Leaving /health mid-read: nobody is left to show the answer.
    return () => {
      generation += 1;
      inFlight?.abort();
    };
  });
</script>

<section
  class="flex min-w-0 flex-col gap-4 panel p-4 sm:p-5"
  aria-labelledby="audit-heading"
>
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <h2 id="audit-heading" class="eyebrow">Registro de comandos</h2>
    <span class="readout">Solo admins · todas las cuentas</span>
  </div>

  <p class="text-sm text-ink-mute">
    Todo lo que se mandó al robot: desde el agente, desde una conversación de
    voz, o directo (botones de VR, la marcha, PARAR). La marcha por VR escribe
    una fila cada ~1 s; filtrá por habilidad para encontrar el resto.
  </p>

  <form class="flex flex-wrap items-end gap-3" onsubmit={applyFilters}>
    <div class="flex min-w-0 flex-1 basis-40 flex-col gap-1.5">
      <label for="audit-skill" class="text-xs text-ink-dim">Habilidad</label>
      <input
        id="audit-skill"
        bind:value={skillInput}
        placeholder="walk_to"
        autocomplete="off"
        spellcheck="false"
        class="h-10 w-full tile px-3 font-mono text-base text-ink placeholder:text-ink-mute focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
      />
    </div>
    <div class="flex flex-col gap-1.5">
      <label for="audit-status" class="text-xs text-ink-dim">Resultado</label>
      <select
        id="audit-status"
        bind:value={filters.status}
        onchange={() => applyFilters()}
        class="h-10 tile px-3 text-base text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
      >
        <option value="">Todos</option>
        <option value="ok">Sin error</option>
        <option value="error">Con error</option>
      </select>
    </div>
    <div class="flex flex-col gap-1.5">
      <label for="audit-source" class="text-xs text-ink-dim">Origen</label>
      <select
        id="audit-source"
        bind:value={filters.source}
        onchange={() => applyFilters()}
        class="h-10 tile px-3 text-base text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
      >
        <option value="">Todos</option>
        <option value="agent">Agente</option>
        <option value="voice">Voz</option>
        <option value="direct">Directo</option>
      </select>
    </div>
    <button
      type="submit"
      disabled={loading}
      class="inline-flex h-10 items-center gap-1.5 rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
    >
      <RefreshCw
        class={[
          "size-3.5",
          loading && "animate-spin motion-reduce:animate-none",
        ]}
        aria-hidden="true"
      />
      {loaded ? "Actualizar" : "Consultar"}
    </button>
  </form>

  {#if error}
    <Callout tone="danger" title="No se pudo leer el registro">
      {error.text}
      {#if error.verbatim}<span
          class="mt-0.5 block readout break-words"
          lang="en">{error.verbatim}</span
        >{/if}
    </Callout>
  {/if}

  <div aria-busy={loading}>
    {#if loading && (!loaded || (superseded && rows.length === 0))}
      <p class="text-sm text-ink-mute" role="status">Leyendo el registro…</p>
    {:else if loaded && rows.length === 0}
      <p class="text-sm text-ink-mute" role="status">
        Ningún comando coincide con estos filtros.
      </p>
    {:else if rows.length}
      <ul
        class={[
          "flex flex-col gap-1.5 transition-opacity",
          superseded && "opacity-50",
        ]}
      >
        {#each rows as row (row.id)}
          {@const outcome = rowOutcome(row)}
          {@const created = new Date(row.createdAt)}
          {@const open = expanded[row.id] === true}
          <li class="tile">
            <button
              type="button"
              aria-expanded={open}
              aria-controls={`audit-${row.id}`}
              onclick={() => (expanded[row.id] = !open)}
              class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-wash-hover focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none sm:grid-cols-[7rem_minmax(0,1fr)_minmax(0,9rem)_auto_auto]"
            >
              <time
                class="readout sm:pt-0.5"
                datetime={created.toISOString()}
                title={exactTime(row.createdAt)}
              >
                {formatSince(created.getTime(), now)}
              </time>
              <span
                class="col-start-1 flex min-w-0 flex-col gap-0.5 sm:col-start-auto"
              >
                <span class="font-mono text-sm break-all text-ink" lang="en"
                  >{row.skillName}</span
                >
                <span class="text-xs {OUTCOME_TONE[outcome.kind]}"
                  >{outcome.text}</span
                >
              </span>
              <span
                class="col-start-1 flex min-w-0 flex-col gap-0.5 sm:col-start-auto"
              >
                <span
                  class="truncate text-xs text-ink-dim"
                  title={row.user?.email}
                >
                  {whoLabel(row.user)}{row.user && row.user.id === myId
                    ? " (vos)"
                    : ""}
                </span>
                <span class="readout">{sourceLabel(row.source)}</span>
              </span>
              <span
                class="row-start-1 justify-self-end readout sm:row-start-auto sm:pt-0.5"
              >
                {formatDuration(row.durationMs)}
              </span>
              <ChevronDown
                class={[
                  "row-start-2 size-4 justify-self-end text-ink-mute transition-transform sm:row-start-auto",
                  open && "rotate-180",
                ]}
                aria-hidden="true"
              />
            </button>
            {#if open}
              <div
                id={`audit-${row.id}`}
                class="flex flex-col gap-2 border-t border-hairline px-3 py-3"
              >
                <p class="readout">{exactTime(row.createdAt)} · id {row.id}</p>
                {#if outcome.detail}
                  <p class="readout break-words text-ink-dim">
                    <span lang="en">{outcome.detail}</span>
                  </p>
                {/if}
                <div class="grid gap-2 lg:grid-cols-2">
                  <div class="flex min-w-0 flex-col gap-1">
                    <h3 class="eyebrow">Parámetros</h3>
                    <pre
                      class="max-h-60 overflow-auto rounded-md bg-trench p-2 font-mono text-2xs text-ink-dim"
                      lang="en">{json(row.params ?? {})}</pre>
                  </div>
                  <div class="flex min-w-0 flex-col gap-1">
                    <h3 class="eyebrow">Resultado</h3>
                    {#if row.result === null || row.result === undefined}
                      <p class="text-xs text-ink-mute">
                        No se guardó resultado.
                      </p>
                    {:else}
                      <pre
                        class="max-h-60 overflow-auto rounded-md bg-trench p-2 font-mono text-2xs text-ink-dim"
                        lang="en">{json(row.result)}</pre>
                    {/if}
                  </div>
                </div>
                {#if row.chatId}
                  <!-- Only the chat's owner can open it; for anyone else the
                       link would 404 into a blank chat, so show the id. -->
                  {#if row.user && row.user.id === myId}
                    <a
                      href={resolve(
                        `chat?id=${encodeURIComponent(row.chatId)}`,
                      )}
                      class="self-start text-xs text-ink-dim underline hover:text-ink"
                    >
                      Abrir la conversación
                    </a>
                  {:else}
                    <p class="readout">chat {row.chatId}</p>
                  {/if}
                {/if}
              </div>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  {#if nextCursor}
    <button
      type="button"
      disabled={loading}
      onclick={() => load(false)}
      class="inline-flex h-10 items-center self-center rounded-lg border border-hairline-strong px-4 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
    >
      {loading ? "Cargando…" : "Cargar más"}
    </button>
  {:else if loaded && rows.length && !superseded}
    <p class="text-center readout">Fin del registro para estos filtros.</p>
  {/if}
</section>
