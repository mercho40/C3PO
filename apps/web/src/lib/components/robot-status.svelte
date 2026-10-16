<script lang="ts">
  // Connection + environment + battery readout for the console topbar. Reads the
  // shared poller, so it agrees with whatever the dashboard and map are showing
  // rather than keeping its own idea of "online".
  //
  // Honest-state rules this chip used to break, and now keeps:
  //  - RobotLive KEEPS the last state when a poll fails (panels show it as the
  //    last reading). This chip read battery and faults from it without checking
  //    `online`, so it said "Offline" next to a confidently coloured "87%" and
  //    "2 fallos" while the dashboard below said "Sin conexión con el robot".
  //    Offline now shows a muted glyph and "—", and no fault count.
  //  - It looked identical against the stub, Isaac Sim and the physical G1, and
  //    the stub's battery is a hard-coded 87 %. Sim vs real is a safety property
  //    (see CLAUDE.md), so the environment is on the chip, and a stub battery is
  //    marked as invented.
  //  - Its words come from the shared link vocabulary, so a lapsed session
  //    reads "Sesión vencida", not "Offline".
  //  - It is NOT a live region. The whole chip used to be role="status", which
  //    is atomic: on the real G1 the battery ticks and faults come and go every
  //    poll, and each change re-read environment + link + battery + faults —
  //    every 2 s. Only a change of the link word is announced now (a dedicated
  //    sr-only region below); the rest is there to read, not to interrupt.
  import type { Component } from "svelte";
  import {
    Battery,
    BatteryFull,
    BatteryLow,
    BatteryMedium,
    BatteryWarning,
    TriangleAlert,
  } from "@lucide/svelte";
  import { getRobotLive } from "#lib/robot/context.js";
  import { LINK, type LinkProblem } from "#lib/robot/link-copy.js";
  import {
    BATTERY_TONE,
    batteryLevel,
    batteryPct,
    type BatteryLevel,
  } from "#lib/robot/format.js";

  const live = getRobotLive();

  const online = $derived(live.online);
  const problem = $derived<LinkProblem>(live.problem ?? "offline");
  const word = $derived(online ? LINK.online : LINK[problem]);
  // Phone spelling, for the one word too long for a phone's topbar row: "Sin
  // acceso a la API" truncated to "SIN ACCESO A LA …" — the part that says
  // what is missing was the part cut off. Screen readers always get `word`.
  const SHORT: Partial<Record<LinkProblem, string>> = { noApi: "Sin API" };
  const shortWord = $derived(online ? word : (SHORT[problem] ?? word));

  // Which robot the console is driving. Kept from the last reading when offline
  // (the bridge's mode does not change under a dropped poll), but muted, because
  // we can no longer see it.
  type Env = "real" | "sim" | "stub";
  const env = $derived.by((): Env | null => {
    const s = live.state;
    if (!s) return null;
    if (s.stub === true || s.env === "stub") return "stub";
    if (s.env === "real") return "real";
    return "sim";
  });
  const ENV_TEXT: Record<Env, string> = {
    real: "Real",
    sim: "Sim",
    stub: "Stub",
  };
  const envSentence = $derived(
    env === "real"
      ? "Robot físico: las órdenes mueven el Unitree G1 real"
      : env === "stub"
        ? "Puente stub: todos los valores son inventados"
        : env === "sim"
          ? `Simulación (${live.state?.env ?? "sim"})`
          : "",
  );
  const ENV_CLASS: Record<Env, string> = {
    real: "border-danger/45 bg-danger/10 text-danger-soft",
    sim: "border-hairline-strong text-ink-dim",
    stub: "border-warn/40 bg-warn/10 text-warn",
  };

  // Only a CURRENT reading is a number. The last one is in the tooltip, labelled.
  const lastPct = $derived(batteryPct(live.state?.battery_pct));
  const pct = $derived(online ? lastPct : null);
  const level = $derived(batteryLevel(pct));
  const simulated = $derived(online && live.simulated);
  // Same bands for glyph and colour (`format.ts`) — they used to switch at
  // different thresholds, so 50 % showed a half glyph in plain ink.
  const ICON: Record<BatteryLevel, Component> = {
    unknown: Battery,
    critical: BatteryWarning,
    low: BatteryLow,
    ok: BatteryMedium,
    full: BatteryFull,
  };
  const BatteryIcon = $derived(ICON[level]);
  // A fabricated value is not coloured as if it meant something — and from
  // `sm` it is labelled with the word the environment tag uses, rather than
  // the bare asterisk it used to carry: "87 %*" read as a footnote with no
  // footnote.
  const batteryClass = $derived(
    simulated ? "text-ink-mute" : BATTERY_TONE[level],
  );
  const batterySentence = $derived(
    pct != null
      ? `Batería ${pct} %${simulated ? " (simulada)" : ""}`
      : online
        ? "Batería sin lectura"
        : lastPct != null
          ? `Batería: sin lectura actual (última: ${lastPct} %)`
          : "Batería: sin lectura actual",
  );

  const faults = $derived(online ? (live.state?.faults?.length ?? 0) : 0);
  const faultWord = $derived(`${faults} ${faults === 1 ? "fallo" : "fallos"}`);
</script>

<!-- The one announcement: the link word, and only when it changes. Present
     from the first render (empty regions inserted later are often missed), and
     its text depends on nothing but `word`, so battery and fault ticks never
     touch it. -->
<span role="status" class="sr-only">Robot: {word}.</span>

<!-- `min-w-0` + `max-w-full`: on a phone this chip has the topbar's second row
     to itself (see dashboard-topbar), but it must still truncate rather than
     push past the edge if a narrow screen meets every optional part at once. -->
<div
  class="flex max-w-full min-w-0 items-center gap-2 tile px-2 py-1.5 sm:gap-3 sm:px-3"
>
  {#if env}
    <span
      class={[
        "shrink-0 rounded-sm border px-1.5 readout leading-4 font-medium tracking-[0.12em] uppercase",
        online ? ENV_CLASS[env] : "border-hairline text-ink-mute",
      ]}
      title={online ? envSentence : `${envSentence} (último entorno conocido)`}
    >
      <span aria-hidden="true">{ENV_TEXT[env]}</span>
      <span class="sr-only">{envSentence}.</span>
    </span>
  {/if}

  <!-- `shrink-0` on the dot and the word's minimum: whatever else gives way,
       "is the robot connected" stays readable. -->
  <span class="flex min-w-[6ch] items-center gap-1.5">
    <!-- Filled when connected, hollow when not: the state has to survive
         greyscale and colour-blindness, so it differs in shape and not only in
         hue. -->
    <span
      class={[
        "size-2 shrink-0 rounded-full",
        online
          ? "bg-ok shadow-(--c3-glow-ok)"
          : "border-[1.5px] border-danger bg-transparent",
      ]}
      aria-hidden="true"
    ></span>
    <!-- The word stays at every width — the short spelling on a phone, the
         full one from `sm`. A 6px dot alone should not carry "is the robot
         connected", which is the question this component exists to answer. -->
    <span
      class={[
        "truncate readout tracking-[0.12em] uppercase",
        online ? "text-ok" : "text-danger-soft",
      ]}
      aria-hidden="true"
    >
      <span class="sm:hidden">{shortWord}</span><span class="hidden sm:inline"
        >{word}</span
      >
    </span>
    <span class="sr-only">{word}.</span>
  </span>

  <span
    class="hidden h-3.5 w-px shrink-0 bg-hairline-strong sm:inline-block"
    aria-hidden="true"
  ></span>

  <span
    class={["flex shrink-0 items-center gap-1.5", batteryClass]}
    title={batterySentence}
  >
    <!-- Glyph and number at every width: on a phone this chip has a row of its
         own now, and the battery is what an operator standing next to the
         robot most wants from it. -->
    <BatteryIcon class="size-4" aria-hidden="true" />
    <span class="readout whitespace-nowrap text-[inherit]" aria-hidden="true">
      {pct != null ? `${pct} %` : "—"}{#if simulated}<span
          class="hidden text-ink-mute sm:inline">&nbsp;stub</span
        >{/if}
    </span>
    <span class="sr-only">{batterySentence}.</span>
  </span>

  {#if faults > 0}
    <span
      class="hidden h-3.5 w-px shrink-0 bg-hairline-strong sm:inline-block"
      aria-hidden="true"
    ></span>
    <span class="flex shrink-0 items-center gap-1 readout text-danger-soft">
      <!-- Icon + count on a phone, the word from `sm` up. -->
      <TriangleAlert class="size-3.5 sm:hidden" aria-hidden="true" />
      <span aria-hidden="true" class="sm:hidden">{faults}</span>
      <span class="hidden sm:inline" aria-hidden="true">{faultWord}</span>
      <span class="sr-only">{faultWord}.</span>
    </span>
  {/if}
</div>
