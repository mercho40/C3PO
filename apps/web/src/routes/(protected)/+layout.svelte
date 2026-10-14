<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { navigating } from "$app/state";
  import AppSidebar from "#lib/components/app-sidebar.svelte";
  import DashboardTopbar from "#lib/components/dashboard-topbar.svelte";
  import EstopBanner from "#lib/components/shell/estop-banner.svelte";
  import NavProgress from "#lib/components/shell/nav-progress.svelte";
  import * as Sidebar from "#lib/components/ui/sidebar/index.js";
  import { PUBLIC_API_URL } from "$app/env/public";
  import { RobotLive } from "#lib/robot/live-state.svelte.js";
  import { VoiceLoopControl } from "#lib/robot/voice-loop.svelte.js";
  import { Gate } from "#lib/robot/gate.svelte.js";
  import { setGate, setRobotLive, setVoiceLoop } from "#lib/robot/context.js";
  import { readPosture } from "#lib/robot/posture.js";
  import { staleness } from "#lib/robot/faults.js";
  import { estop } from "#lib/robot/estop.svelte.js";
  import { isStopHotkey } from "#lib/robot/estop.js";

  let { children, data } = $props();

  // One poller for the whole console, seeded from the layout load so the first
  // paint already has real values. Pages read it via `getRobotLive()`; keeping
  // it here means the trail and odometer survive navigation between the
  // dashboard and the map.
  const live = untrack(() => new RobotLive(data.state, data.online));
  setRobotLive(live);
  // Same for the voice loop: whether the robot is listening is shown in the
  // topbar on every page, not only where the switch lives.
  const voice = setVoiceLoop(new VoiceLoopControl(PUBLIC_API_URL));
  // And whether Nav2 is armed to drive it — the other state that must be
  // visible everywhere, because the agent can arm it from a chat.
  const gate = setGate(new Gate());
  onMount(() => {
    live.start();
    voice.start();
    gate.start(2000);
    // The e-stop store is module-level (every StopButton shows the same press),
    // so it is handed telemetry here, in the browser only — never during SSR,
    // where module state would be shared between requests.
    const disconnect = estop.connect(
      () => {
        // Same meaning as StopButton's fill: a loaded gait controller counts,
        // because it walks the moment anything sends it a velocity.
        const stub = live.simulated;
        const staleS = staleness(live.state?.faults).posture;
        const p = readPosture(live.state?.posture, live.online, {
          staleS,
          stub,
        });
        return {
          online: live.online,
          moving: p.locomotion || p.load === "moving",
          // Only a real, current posture can confirm a stop (see Telemetry).
          known: !stub && staleS === null && p.load !== "unknown",
          sampledAt:
            live.lastOkAt != null
              ? live.lastOkAt - (live.latencyMs ?? 0)
              : null,
          env: live.state?.env ?? null,
        };
      },
      () => void live.tick(),
    );
    return () => {
      disconnect();
      live.stop();
      voice.stop();
      gate.stop();
    };
  });

  // Shift+Space stops the robot from any console page — except while typing, so
  // a capital letter followed by a space in the chat never halts anything. A
  // desktop operator watching the map or the camera used to have no keyboard
  // path to the stop short of tabbing to it.
  function onKeydown(e: KeyboardEvent) {
    const hit = isStopHotkey({
      key: e.key,
      code: e.code,
      shiftKey: e.shiftKey,
      ctrlKey: e.ctrlKey,
      metaKey: e.metaKey,
      altKey: e.altKey,
      repeat: e.repeat,
      target: e.target instanceof HTMLElement ? e.target : null,
    });
    if (!hit) return;
    // CAPTURE phase, and stopPropagation: the focused control must never see
    // this key. In the bubble phase a focused "Adelante" (hold-to-walk) or "3 ·
    // Habilitar marcha" (hold-to-confirm) acted on the Space keydown BEFORE
    // this ran — so Shift+Space started a walk, or armed a high-danger skill,
    // in the same instant it asked for a stop.
    e.preventDefault();
    e.stopPropagation();
    swallowSpaceUp = true;
    void estop.press();
  }

  // A button activates on Space KEYUP, not keydown. Swallow the keyup that
  // belongs to a stop-hotkey press too — even if Shift was released first — so
  // it cannot click whatever has focus.
  let swallowSpaceUp = false;
  function onKeyup(e: KeyboardEvent) {
    if (!swallowSpaceUp || (e.key !== " " && e.code !== "Space")) return;
    swallowSpaceUp = false;
    e.preventDefault();
    e.stopPropagation();
  }
</script>

<svelte:window onkeydowncapture={onKeydown} onkeyupcapture={onKeyup} />

<Sidebar.Provider
  open={data.sidebarOpen}
  class="h-svh overflow-hidden font-display text-foreground"
>
  <AppSidebar />
  <!-- `min-w-0`: the inset is a flex sibling of the sidebar, and flex items
       refuse to shrink below their content by default. Without it, between
       768px (where the sidebar stops being a sheet) and roughly 1024px the
       dashboard's headline held the inset wider than the viewport and the right
       edge of every panel was clipped off-screen.

       The ambient wash is the inset's own background now, not an absolute layer
       under a `relative z-10` content wrapper. That wrapper created a stacking
       context, and nothing inside a stacking context can rise above a modal
       overlay portalled to <body> — which is exactly what the topbar's e-stop
       has to do. Keep every ancestor of the topbar free of z-index, transform,
       filter and isolation. -->
  <Sidebar.Inset class="relative min-w-0 overflow-hidden bg-background ambient">
    <NavProgress />

    <div
      class="flex min-h-0 min-w-0 flex-1 flex-col gap-4 px-4 pb-4 sm:px-6 sm:pb-6 lg:px-7 lg:pb-7"
    >
      <DashboardTopbar />
      <EstopBanner />
      <!-- aria-busy on the page region, not the whole inset: the topbar (and
           the e-stop's announcements) must stay live while a page loads. -->
      <div
        class="min-h-0 min-w-0 flex-1 overflow-y-auto"
        aria-busy={navigating.to ? "true" : undefined}
      >
        {@render children()}
      </div>
    </div>
  </Sidebar.Inset>
</Sidebar.Provider>
