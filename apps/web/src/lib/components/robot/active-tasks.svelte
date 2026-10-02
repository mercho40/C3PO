<!--
  What the robot is DOING, task by task — on the page that claims to answer
  "what is it doing right now".

  Every motion skill registers a bridge task (walk_to, turn, gestures, presets,
  the VR teleop session), and back exposes GET /tasks and a per-task cancel —
  but nothing in the console called either, so a 20 s walk was invisible here
  and the only control was the global PARAR.

  Honest about cancel: a graceful cancel answers HTTP 200 even when it did
  nothing (`ok: false` + a reason), so the body is read, and "Cancelación
  pedida" stays up until a poll shows the task actually ended. The bridge has
  not yet verified an in-flight cancel of a live walk on hardware, and the
  panel says so — PARAR is the stop that is known to work.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { toast } from "svelte-sonner";
  import { X } from "@lucide/svelte";
  import { createApi } from "$lib/api";
  import { Poller, type PollResult } from "$lib/robot/poller.svelte";
  import { LINK_DETAIL, linkFromError } from "$lib/robot/link-copy";

  const TIMEOUT = "El servidor tardó demasiado en responder.";

  /**
   * Why the task list could not be read, in words about THIS list. The
   * generic "offline" sentence says "lo que ves es la última lectura" of the
   * ROBOT'S STATE, which is not what failed here.
   */
  function failure(status: number, value: unknown, aborted: boolean): string {
    // Eden's shape for a fetch that threw (`{status: 503, value: Error}`):
    // the browser never reached back — or the timeout fired.
    if (value instanceof Error) return aborted ? TIMEOUT : LINK_DETAIL.noApi;
    const problem = linkFromError(status, value);
    return problem === "offline"
      ? `No se pudo leer la lista de tareas (HTTP ${status}).`
      : LINK_DETAIL[problem];
  }

  type Task = {
    task_id: string;
    skill_name: string;
    status: "running" | "completed" | "cancelled" | "failed" | string;
    progress: number;
    phase: string | null;
    error: string | null;
    started_at: number;
    ended_at: number | null;
    duration_s: number;
  };

  class Tasks extends Poller {
    active = $state<Task[]>([]);
    recent = $state<Task[]>([]);
    reason = $state<string | null>(null);

    constructor() {
      // A background dashboard has nobody to show tasks to, and every poll is
      // an MCP tool call on the robot's Jetson.
      super({ timeoutMs: 3000, pauseWhenHidden: true });
    }

    protected async poll(signal: AbortSignal): Promise<PollResult> {
      let res;
      try {
        res = await createApi(fetch).tasks.get({
          query: { include_recent: "true" },
          fetch: { signal },
        });
      } catch {
        this.reason = signal.aborted ? TIMEOUT : LINK_DETAIL.noApi;
        return "error";
      }
      if (res.error) {
        const status = res.error.status as number;
        if (status === 401) return "unauthorized";
        this.reason = failure(status, res.error.value, signal.aborted);
        return "error";
      }
      const body = (res.data ?? {}) as { active?: Task[]; recent?: Task[] };
      this.active = body.active ?? [];
      this.recent = (body.recent ?? []).filter((t) => t.status !== "running");
      this.reason = null;
      return "ok";
    }
  }

  const tasks = new Tasks();
  onMount(() => {
    tasks.start(2000);
    return () => tasks.stop();
  });

  // Cancels requested from this tab, until a poll shows the task gone.
  let cancelling = $state<Record<string, boolean>>({});
  const pending = $derived(
    Object.keys(cancelling).filter((id) =>
      tasks.active.some((t) => t.task_id === id),
    ),
  );

  const REASON: Record<string, string> = {
    unknown_task_id: "el puente ya no conoce esa tarea",
    cancel_already_requested: "la cancelación ya estaba pedida",
  };

  async function cancel(task: Task) {
    cancelling = { ...cancelling, [task.task_id]: true };
    try {
      const { data, error } = await createApi(fetch)
        .tasks({ task_id: task.task_id })
        .cancel.post({ mode: "graceful" });
      if (error) {
        const status = error.status as number;
        throw new Error(
          status === 401
            ? LINK_DETAIL.expired
            : failure(status, error.value, false),
        );
      }
      const body = (data ?? {}) as { ok?: boolean; reason?: string };
      if (body.ok === false) {
        const why = body.reason ?? "";
        // Already requested is not a failure worth undoing the label for.
        if (why !== "cancel_already_requested") {
          const { [task.task_id]: _, ...rest } = cancelling;
          cancelling = rest;
        }
        // A toast cannot mark a span lang="en", so an unknown reason is
        // given as a CODE, introduced in Spanish, never as a bare English
        // sentence.
        toast.warning("La tarea no se canceló.", {
          description:
            REASON[why] ??
            (why.startsWith("task_not_running")
              ? "la tarea ya había terminado"
              : why
                ? `Código del puente: ${why}`
                : undefined),
        });
      }
    } catch (err) {
      const { [task.task_id]: _, ...rest } = cancelling;
      cancelling = rest;
      toast.error("No se pudo pedir la cancelación.", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  }

  // The list on screen is the LAST good reading once a poll has failed. Its
  // tasks may have ended since; "Cancelar" on one is a request about a
  // reading we can no longer vouch for, so it is disabled until the list is
  // current again (PARAR does not depend on this list).
  const stale = $derived(tasks.reason !== null);
  const countWord = $derived(
    tasks.active.length === 0
      ? "Sin tareas en curso."
      : tasks.active.length === 1
        ? "1 tarea en curso."
        : `${tasks.active.length} tareas en curso.`,
  );

  const STATUS: Record<string, { word: string; tone: string }> = {
    completed: { word: "completada", tone: "text-ok" },
    cancelled: { word: "cancelada", tone: "text-warn" },
    failed: { word: "falló", tone: "text-danger-soft" },
  };
</script>

<div class="flex flex-col gap-2">
  <h2 class="eyebrow">
    En curso{stale && tasks.active.length > 0 ? " · última lectura" : ""}
  </h2>

  <!-- The only live part: how many tasks there are. The list itself used to be
       the live region, and its per-task "N s" re-announced every task every
       2 s while anything was running. -->
  {#if tasks.loaded && !stale}
    <p class="sr-only" role="status">{countWord}</p>
  {/if}

  {#if tasks.reason && tasks.active.length === 0}
    <p class="text-sm text-ink-mute">{tasks.reason}</p>
  {:else if !tasks.loaded}
    <p class="text-sm text-ink-mute">Consultando tareas…</p>
  {:else if tasks.active.length === 0}
    <p class="text-sm text-ink-mute">Sin tareas en curso.</p>
  {:else}
    {#if stale}
      <!-- Shown whenever set: it used to hide as soon as the list had rows,
           leaving dimmed tasks and a live "Cancelar" with no word on why. -->
      <p class="text-sm text-warn">
        No se pudo actualizar la lista; estas son las tareas de la última
        lectura. {tasks.reason}
      </p>
    {/if}
    <ul class="flex flex-col gap-2.5">
      {#each tasks.active as task (task.task_id)}
        {@const asked = pending.includes(task.task_id)}
        <li class="flex flex-col gap-1.5 tile p-3">
          <div class="flex items-center gap-2">
            <span
              class={[
                "min-w-0 flex-1 truncate font-mono text-sm",
                stale ? "text-ink-mute" : "text-ink",
              ]}
              lang="en">{task.skill_name}</span
            >
            <span class="readout">{Math.round(task.duration_s)} s</span>
            <!-- 40 px on touch, 32 px with a pointer. -->
            <button
              type="button"
              onclick={() => cancel(task)}
              disabled={asked || stale}
              title={stale
                ? "La lista no está actualizada: no se puede cancelar desde acá"
                : undefined}
              class="inline-flex min-h-10 items-center gap-1 tile-interactive px-3 text-xs text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60 sm:min-h-8 sm:px-2.5"
            >
              <X class="size-3.5" aria-hidden="true" />
              {asked ? "Cancelación pedida" : "Cancelar"}
              <span class="sr-only" lang="en">{task.skill_name}</span>
            </button>
          </div>
          <div
            class="h-1 overflow-hidden rounded-full bg-hairline-strong"
            role="progressbar"
            aria-label="Progreso de {task.skill_name}"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={Math.round(task.progress * 100)}
          >
            <div
              class={[
                "h-full rounded-full transition-[width] duration-500",
                stale ? "bg-ink-mute" : "bg-peri",
              ]}
              style:width="{Math.round(task.progress * 100)}%"
            ></div>
          </div>
          {#if task.phase}
            <span class="readout" lang="en">{task.phase}</span>
          {/if}
        </li>
      {/each}
    </ul>
    <p class="text-xs leading-relaxed text-ink-mute">
      Cancelar pide una parada suave. Todavía no está verificado sobre una
      caminata real en curso: ante la duda, usá PARAR (<kbd class="font-mono"
        >Shift+Espacio</kbd
      >).
    </p>
  {/if}

  {#if tasks.recent.length > 0}
    <details class="group">
      <summary
        class="cursor-pointer list-none rounded readout text-ink-mute hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none [&::-webkit-details-marker]:hidden"
      >
        <span class="group-open:hidden"
          >Ver recientes ({tasks.recent.length})</span
        >
        <span class="hidden group-open:inline">Ocultar recientes</span>
      </summary>
      <ul class="mt-2 flex flex-col gap-1.5 text-sm">
        {#each tasks.recent.slice(0, 5) as task (task.task_id)}
          {@const st = STATUS[task.status] ?? {
            word: task.status,
            tone: "text-ink-mute",
          }}
          <li class="flex flex-col">
            <span class="flex items-baseline justify-between gap-3">
              <span class="font-mono text-ink" lang="en">{task.skill_name}</span
              >
              <span class="readout {st.tone}">{st.word}</span>
            </span>
            {#if task.error}
              <span class="readout break-words text-danger-soft" lang="en"
                >{task.error}</span
              >
            {/if}
            <!-- Duration, not "ended N s ago": the bridge computes the first
                 on its own clock; the second would set the Jetson's epoch
                 against the browser's, and the two are not synchronised. -->
            <span class="readout">duró {Math.round(task.duration_s)} s</span>
          </li>
        {/each}
      </ul>
    </details>
  {/if}
</div>
