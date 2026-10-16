<!--
  /health — "Salud": one row per subsystem, worst first, with what to do.

  The data was already flowing and being thrown away: get_state's `raw` block,
  the catalogue envelope, the telemetry routes, the camera's /status, the
  costmap's headers, the Nav2 gate. Classification lives in
  `#lib/robot/health.ts` (tested); this page composes it.

  Sources: the console's shared pollers for the link, battery, faults, gate
  and voice loop (re-polling /state here would double the get_state load on the
  Jetson), plus one `HealthProbe` for everything else, paused while the tab is
  hidden.

  The top line is the worst tone across rows, and it never says "todo en
  orden" while any row is unknown — a subsystem nobody could read is a gap,
  not a pass.

  The page title comes from the topbar (`#lib/nav.ts`).
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { RefreshCw } from "@lucide/svelte";
  import { PUBLIC_ROBOT_CAM_URL, PUBLIC_SIM_CAM_HOST } from "$app/env/public";
  import HealthRowView from "#lib/components/health/health-row.svelte";
  import AuditLog from "#lib/components/health/audit-log.svelte";
  import { getGate, getRobotLive, getVoiceLoop } from "#lib/robot/context.js";
  import { HealthProbe } from "#lib/robot/health.svelte.js";
  import {
    TONE_LABEL,
    TONE_RANK,
    TONE_TEXT,
    classifyBattery,
    classifyCamera,
    classifyCatalogue,
    classifyFaults,
    classifyGate,
    classifyLink,
    classifyMap,
    classifyPerception,
    classifyServer,
    classifyVoice,
    overall,
    type LinkInput,
  } from "#lib/robot/health.js";
  import { formatSince } from "#lib/robot/format.js";

  let { data } = $props();

  const live = getRobotLive();
  const gate = getGate();
  const voice = getVoiceLoop();

  const cameraUrl = PUBLIC_ROBOT_CAM_URL.trim();
  const simHost = PUBLIC_SIM_CAM_HOST.trim();
  const probe = new HealthProbe(cameraUrl);

  // Ages are worded relative to now, so the page needs a clock. One second is
  // the resolution every age on this page is printed at.
  let now = $state(Date.now());
  let pageProtocol = $state("http:");

  onMount(() => {
    pageProtocol = location.protocol;
    probe.start(5000);
    const clock = setInterval(() => (now = Date.now()), 1000);
    return () => {
      probe.stop();
      clearInterval(clock);
    };
  });

  const link = $derived<LinkInput>({
    online: live.online,
    problem: live.problem,
    latencyMs: live.latencyMs,
    simulated: live.simulated,
    state: live.state,
    lastOkAt: live.lastOkAt,
    loaded: live.loaded || live.state !== null,
  });

  const rows = $derived([
    classifyServer(probe.server, now),
    classifyCatalogue(probe.catalogue, now),
    classifyLink(link, now),
    classifyBattery(link, now),
    classifyFaults(link, now),
    classifyGate(
      {
        status: gate.status,
        reason: gate.reason,
        reasonFromBridge: gate.reasonFromBridge,
        loaded: gate.loaded,
        lastOkAt: gate.lastOkAt,
      },
      now,
    ),
    classifyPerception(probe.surroundings, now),
    classifyVoice(
      probe.voice,
      { state: voice.state, reason: voice.reason },
      now,
    ),
    classifyCamera(
      probe.camera,
      { robotUrl: cameraUrl, simHost, pageProtocol },
      now,
    ),
    classifyMap(probe.costmap, now),
  ]);

  // Worst first, stable within a tone so rows do not shuffle on every tick.
  const ordered = $derived(
    rows
      .map((row, i) => ({ row, i }))
      .sort(
        (a, b) => TONE_RANK[b.row.tone] - TONE_RANK[a.row.tone] || a.i - b.i,
      )
      .map(({ row }) => row),
  );
  const summary = $derived(overall(rows));
</script>

<div class="flex flex-col gap-4 pb-2">
  <section
    class="flex min-w-0 flex-col gap-3 panel-hero p-5 sm:p-6"
    aria-labelledby="health-summary-heading"
  >
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 id="health-summary-heading" class="eyebrow">Estado general</h2>
      <button
        type="button"
        onclick={() => probe.refresh()}
        class="inline-flex h-9 items-center gap-1.5 rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
      >
        <RefreshCw class="size-3.5" aria-hidden="true" />
        Actualizar
      </button>
    </div>
    <div role="status" aria-live="polite" class="flex flex-col gap-1">
      <p class="stamp-quiet text-xl sm:text-2xl {TONE_TEXT[summary.tone]}">
        {TONE_LABEL[summary.tone]}
      </p>
      <p class="text-sm text-ink-dim">{summary.summary}</p>
    </div>
    <p class="readout">
      Consulta cada 5 s mientras esta pestaña está visible{probe.lastOkAt
        ? ` · última respuesta del servidor ${formatSince(probe.lastOkAt, now)}`
        : ""}
    </p>
  </section>

  <section
    class="flex min-w-0 flex-col gap-3"
    aria-labelledby="health-rows-heading"
  >
    <h2 id="health-rows-heading" class="eyebrow">Subsistemas</h2>
    <div class="grid gap-3 xl:grid-cols-2">
      {#each ordered as row (row.id)}
        <HealthRowView {row} />
      {/each}
    </div>
  </section>

  {#if data.isAdmin}
    <AuditLog {now} />
  {/if}
</div>
