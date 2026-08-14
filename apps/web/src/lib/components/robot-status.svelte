<script lang="ts">
  // Connection + battery readout for the console topbar. Reads the shared
  // poller, so it agrees with whatever the dashboard and map are showing rather
  // than keeping its own idea of "online".
  import { BatteryLow, BatteryMedium, BatteryFull, Plug } from "@lucide/svelte";
  import { getRobotLive } from "$lib/robot/context";

  const live = getRobotLive();

  const online = $derived(live.online);
  const battery = $derived(
    live.state?.battery_pct != null ? Math.round(live.state.battery_pct) : null,
  );
  const faults = $derived(live.state?.faults?.length ?? 0);

  // Battery is the one number worth colouring: below 20% the operator needs to
  // get the robot to a dock, and that shouldn't read the same as "fine".
  const BatteryIcon = $derived(
    battery == null
      ? Plug
      : battery <= 20
        ? BatteryLow
        : battery <= 60
          ? BatteryMedium
          : BatteryFull,
  );
  const batteryTone = $derived(
    battery == null
      ? "text-ink-mute"
      : battery <= 20
        ? "text-danger-soft"
        : battery <= 40
          ? "text-warn"
          : "text-ink",
  );
</script>

<div
  class="flex items-center gap-2 tile px-2.5 py-1.5 sm:gap-3 sm:px-3"
  role="status"
  aria-label={online ? "Robot conectado" : "Robot sin conexión"}
>
  <span class="flex items-center gap-1.5">
    <!-- Filled when connected, hollow when not: the state has to survive
         greyscale and colour-blindness, so it differs in shape and not only in
         hue. -->
    <span
      class="size-2 rounded-full {online
        ? 'bg-ok shadow-[0_0_10px_rgba(94,231,161,0.7)]'
        : 'border-[1.5px] border-danger bg-transparent'}"
    ></span>
    <!-- The word stays at every width. Dropping it below 640px left a 6px dot
         as the only carrier of "is the robot connected", which is the question
         this component exists to answer. -->
    <span
      class="readout tracking-[0.12em] uppercase {online
        ? 'text-ok'
        : 'text-danger-soft'}"
    >
      {online ? "En línea" : "Offline"}
    </span>
  </span>

  <span class="hidden h-3.5 w-px bg-hairline-strong sm:inline-block"></span>

  <span class="flex items-center gap-1.5 {batteryTone}">
    <!-- The glyph stays at every width — it is shape-coded (low/medium/full), so
         it survives greyscale. The number is what gives way on a phone, because
         the topbar also has to hold the connection word and a 44px stop, and the
         exact percentage is on the dashboard hero anyway. -->
    <BatteryIcon class="size-4" />
    <span class="hidden readout text-[inherit] sm:inline"
      >{battery != null ? `${battery}%` : "—"}</span
    >
  </span>

  {#if faults > 0}
    <span class="hidden h-3.5 w-px bg-hairline-strong sm:inline-block"></span>
    <span class="readout text-danger-soft">
      {faults}
      {faults === 1 ? "fallo" : "fallos"}
    </span>
  {/if}
</div>
