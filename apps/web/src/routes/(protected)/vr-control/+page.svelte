<script lang="ts">
  /**
   * VR control — the Quest 3 teleop page: hold-to-walk buttons, WebXR head-yaw
   * turning, the arm mirror over the teleop stream, bring-up and preset
   * gestures, and a mirror of the robot's own camera. Meant to be opened
   * directly in the Quest 3's browser, hence 48 px+ targets for everything
   * reachable in-session, and the whole page doubles as a WebXR `dom-overlay`
   * root so the controls stay usable while a VR session is active.
   *
   * The state and the dispatch live in `$lib/robot/vr-control.svelte.ts` (the
   * safety rules and their history came with them); the decisions that can be
   * tested without a browser live in `$lib/robot/vr-logic.ts`; the panels are
   * `$lib/components/vr/*`. This file wires them to the shared robot state.
   *
   * `/invoke` is admin-only for every non-safety skill (`apps/back/src/
   * routes/skills.ts`), so whoever logs in here needs `role: "admin"` — except
   * for walking and head-yaw over the teleop stream, which bypass apps/back.
   */
  import { onDestroy, onMount, untrack } from "svelte";
  import StopButton from "$lib/components/stop-button.svelte";
  import Callout from "$lib/components/callout.svelte";
  import RobotCameraView from "$lib/components/robot-camera-view.svelte";
  import AdminBanner from "$lib/components/vr/admin-banner.svelte";
  import BringUpPanel from "$lib/components/vr/bring-up-panel.svelte";
  import GesturePanel from "$lib/components/vr/gesture-panel.svelte";
  import TeleopPanel from "$lib/components/vr/teleop-panel.svelte";
  import VrHero from "$lib/components/vr/vr-hero.svelte";
  import VrEstopLine from "$lib/components/vr/vr-estop-line.svelte";
  import VrStrip from "$lib/components/vr/vr-strip.svelte";
  import WalkPanel from "$lib/components/vr/walk-panel.svelte";
  import XrAlerts from "$lib/components/vr/xr-alerts.svelte";
  import { getRobotLive } from "$lib/robot/context";
  import { readPosture } from "$lib/robot/posture";
  import { MotionTracker } from "$lib/robot/motion";
  import { staleness } from "$lib/robot/faults";
  import { VrControl } from "$lib/robot/vr-control.svelte";
  import { readinessFor } from "$lib/webxr/menu-layer";
  import {
    GESTURE_FSM,
    WALK_FSM,
    envBanner,
    envLabel,
    headsetMenu,
    isAdbLink,
    isRealHardware,
    postureGate,
    realBadge,
  } from "$lib/robot/vr-logic";

  let { data } = $props();

  const live = getRobotLive();
  // Same reading as the dashboard: motion is measured from consecutive poses
  // rather than read off the FSM token, so a gait controller standing still at
  // zero stick (FSM 500/501/801/802) is not drawn in cyan, and a stale FSM is
  // labelled as history.
  const stale = $derived(staleness(live.state?.faults));
  const motionTracker = new MotionTracker();
  const motion = $derived(
    motionTracker.observe(
      live.state?.pose ?? null,
      live.lastOkAt,
      stale.pose !== null,
    ),
  );
  const posture = $derived(
    readPosture(live.state?.posture, live.online, {
      motion,
      staleS: stale.posture,
      stub: live.simulated,
      // Same link word as the topbar, and no "última lectura" before any
      // reading has arrived.
      problem: live.problem,
      hasReading: live.state !== null,
    }),
  );
  /** The raw FSM token, only while it is current. */
  const postureToken = $derived(
    live.online ? (live.state?.posture ?? null) : null,
  );
  // From the (protected) layout, which reads the session. Refreshed by the
  // admin banner's "Volver a verificar el rol" (invalidateAll).
  const isAdmin = $derived(data.isAdmin === true);

  const ctl = new VrControl({
    posture: () => (live.online ? (live.state?.posture ?? null) : null),
    menuItems: () => headsetMenu(data.worksReal, data.catalogue.failed),
  });

  // Whether the robot can act at all, pushed to the ONE place the operator is
  // actually looking. `bridgeHolds`, not `streaming`: a socket that is open
  // but stalled is not commanding anything, and the operator needs to know
  // that as much as about one that was never opened.
  const readiness = $derived(
    readinessFor(
      live.state?.posture,
      live.online,
      live.state?.faults,
      ctl.bridgeHolds,
    ),
  );
  $effect(() => {
    const r = readiness;
    untrack(() => ctl.setReadiness(r));
  });

  // The bridge's mode, LIVE. This used to be `data.env` from a one-shot
  // server load: open the page during bring-up (very easy in a rushed setup)
  // and the "not in real mode" banner, the missing camera panel and the
  // in-headset "la cámara ni siquiera se intentó" all stayed until a reload —
  // in a headset. The shared poller refreshes `state.env` every 2 s.
  //
  // Offline, the mode is unknown; the last reading the poller actually got
  // (`live.state` is kept across failures) still decides whether a camera
  // connection is worth opening, but the banner says "sin confirmar" rather
  // than "not real". It used to fall back to the page-load seed instead, so a
  // single timed-out poll unmounted a working camera panel — see
  // `isRealHardware`.
  const liveEnv = $derived(live.online ? (live.state?.env ?? null) : null);
  const lastEnv = $derived(live.state?.env ?? data.state?.env ?? null);
  const realHardware = $derived(isRealHardware(liveEnv, lastEnv));
  const banner = $derived(envBanner(live.online, liveEnv, lastEnv));

  const walkGate = $derived(
    postureGate(live.state?.posture, live.online, WALK_FSM),
  );
  const gestureGate = $derived(
    postureGate(live.state?.posture, live.online, GESTURE_FSM),
  );

  let overlayRoot = $state<HTMLDivElement>();
  let hostname = $state("");

  onMount(() => {
    hostname = location.hostname;
    return ctl.mount();
  });

  onDestroy(() => ctl.destroy());

  // An $effect rather than a one-shot in onMount, because `realHardware` can
  // become true LATER and something has to notice: open the page while the
  // bridge is still coming up and it is false, so the camera never started,
  // its Reconectar button was not rendered at all, and the headset got an empty
  // layer. It is live now, so this fires the moment the bridge reports `real`.
  $effect(() => {
    if (!realHardware) return;
    untrack(() => ctl.ensureCamera());
  });

  const adb = $derived(isAdbLink(hostname, ctl.vrSupported));
</script>

<!--
  No <svelte:head><title>: the topbar owns tab titles ("Control VR · C3PO"), and
  no in-page <h1> outside VR — the topbar's is the page title. In VR the topbar
  is not in the overlay, so the strip carries a small one.
-->
<div
  bind:this={overlayRoot}
  class="flex h-full min-h-0 flex-col gap-4 overflow-y-auto bg-background pb-2"
>
  <!--
    The e-stop, mounted INSIDE the overlay root on purpose.

    WebXR's dom-overlay renders exactly one element and its descendants, and
    the console's own PARAR lives in DashboardTopbar — a sibling in the
    (protected) layout, outside this div. So while the headset is on, the
    topbar simply is not there: the person actually driving the robot had no
    way to stop it, and the only reachable e-stop was the one on a screen they
    could not see.

    Same component as the topbar's rather than a second implementation: it
    reports what it observes instead of what it hopes, and one e-stop with one
    set of semantics is worth more than two that might drift. (Do not remove it
    as a "duplicate" of the topbar's: inside the headset it is the only one.)

    Rendered only WHILE the session is live. Outside the headset the topbar's
    PARAR is always on screen, and two identical stop buttons a few hundred
    pixels apart read as two different things to stop. Inside it, the topbar is
    gone and this is the stop — together with a compact copy of the e-stop's
    failure message and its "Descartar" (`VrEstopLine`), which would otherwise
    be stranded in the shell's banner, outside the overlay too.

    The in-session strip lives in the same sticky bar, so Recentrar and Salir de
    VR are never below a column of opaque panels.
  -->
  {#if ctl.vrActive}
    <div
      class="sticky top-0 z-20 flex flex-col gap-2 border-b border-hairline bg-abyss/95 py-2 backdrop-blur"
    >
      <div class="flex items-center justify-between gap-3">
        <span class="eyebrow">Parada de emergencia</span>
        <StopButton />
      </div>
      <VrEstopLine />
      <VrStrip {ctl} {posture} />
    </div>
  {/if}

  <!-- One polite region for "what just happened" on any tile, so a result
       inside a button is not silent to a screen reader. -->
  <p class="sr-only" role="status" aria-live="polite">{ctl.announcement}</p>

  {#if ctl.linkLost}
    <Callout
      tone="danger"
      title={adb
        ? "Se perdió el enlace USB"
        : "Se perdió la conexión con el servidor de la consola"}
    >
      {#if adb}
        <!-- `adb reverse` forwards live on the USB connection: unplug or jostle
             the cable and every forward vanishes at once, which from inside the
             headset is indistinguishable from the server being down (that is
             how it presented on 2026-08-20). The probe is a static file, so this
             is the path to the console — not the bridge, not the robot. -->
        La página ya no alcanza su propio servidor: los reenvíos de
        <code>adb reverse</code> se cayeron, normalmente porque se desconectó el
        cable del visor. En la Mac: <code>./scripts/quest_setup.sh</code>.
        Mientras tanto nada de esta página llega al robot, tampoco PARAR: los
        pulsos de marcha se agotan solos en un segundo, pero si hay que frenarlo
        ya, usá el control del robot.
      {:else}
        La página no alcanza su propio servidor. Mientras tanto, los botones de
        esta página — PARAR incluido — pueden no llegar al robot.
      {/if}
    </Callout>
  {/if}

  {#if ctl.vrActive}
    <XrAlerts {ctl} {realHardware} />
  {:else}
    <VrHero
      {ctl}
      {posture}
      onenter={(observe) => void ctl.enterVr(overlayRoot, observe)}
    />
  {/if}

  {#if banner?.kind === "offline"}
    <Callout tone="warn" title="Modo del puente sin confirmar">
      Sin conexión con el robot, así que no se sabe si el puente está en modo
      real{banner.lastEnv
        ? ` (última lectura: ${envLabel(banner.lastEnv)})`
        : ""}. Caminar, bailar y el espejo de brazos solo funcionan contra
      hardware real.
    </Callout>
  {:else if banner?.kind === "not_real"}
    <Callout tone="warn" title="El puente no está en modo real">
      Modo: {envLabel(banner.env)}. Caminar, bailar y el espejo de brazos solo
      funcionan contra hardware real — acá el robot no se va a mover.
    </Callout>
  {/if}

  {#if !isAdmin}
    <AdminBanner />
  {/if}

  <div class="grid items-start gap-4 lg:grid-cols-2">
    {#if realHardware}
      <!-- Shares the ONE camera connection the headset uses (see
           `VrControl.connectCamera`); compact in VR, where the XR layer is
           already drawing the full picture behind the overlay. -->
      <section
        class="flex flex-col gap-3 panel p-4"
        aria-labelledby="vr-cam-title"
      >
        <h2 id="vr-cam-title" class="eyebrow">Cámara del robot</h2>
        <div
          class={[
            "overflow-hidden rounded-lg",
            ctl.vrActive ? "aspect-video max-h-48" : "aspect-video",
          ]}
        >
          <RobotCameraView
            state={ctl.camState}
            detail={ctl.camDetail || null}
            url={ctl.camFrameUrl}
            status={ctl.camStatus}
            onreconnect={() => ctl.reconnectCamera()}
            compact={ctl.vrActive}
          />
        </div>
      </section>
    {/if}

    <BringUpPanel
      {ctl}
      posture={postureToken}
      postureLabel={posture.label}
      load={posture.load}
      online={live.online}
      dangerLevels={data.dangerLevels}
    />

    <WalkPanel
      {ctl}
      gate={walkGate}
      postureLabel={posture.label}
      {isAdmin}
      badge={realBadge(data.worksReal, "walk_velocity")}
    />

    <TeleopPanel {ctl} />

    <div class="lg:col-span-2">
      <GesturePanel
        {ctl}
        gate={gestureGate}
        postureLabel={posture.label}
        worksReal={data.worksReal}
        dangerLevels={data.dangerLevels}
        catalogue={data.catalogue}
        gestureNames={data.gestureNames}
        gestureTool={data.gestureTool}
      />
    </div>
  </div>
</div>
