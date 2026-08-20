<script lang="ts">
  /**
   * VR control — the Quest 3 teleop feature, end to end: hold-to-walk
   * buttons, WebXR head-yaw turning, one-shot preset gestures, and a mirror
   * of the robot's own camera feed, all dispatched through `POST
   * /skills/:name/invoke` (the same admin-gated raw control surface
   * `stop-button.svelte` already uses) or, for the camera, the relay in
   * `apps/perception`'s vision container. This page is meant to be
   * opened directly in the Quest 3's browser, hence oversized touch/pointer
   * targets throughout, and the whole page doubles as a WebXR `dom-overlay`
   * root so the buttons stay usable while a VR session is active.
   *
   * `/invoke` is admin-only for every non-safety skill (`apps/back/src/
   * routes/skills.ts`), so whoever logs in here needs `role: "admin"`.
   */
  import { onDestroy, onMount } from "svelte";
  import { browser } from "$app/environment";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { env } from "$env/dynamic/public";
  import {
    ArrowUp,
    ArrowDown,
    Glasses,
    Hand,
    Link2,
    Link2Off,
    Music,
    Handshake,
    HeartHandshake,
    PartyPopper,
    RotateCcw,
    RotateCw,
    TriangleAlert,
    Video,
    VideoOff,
  } from "@lucide/svelte";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import StopButton from "$lib/components/stop-button.svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { createApi } from "$lib/api";
  import { getRobotLive } from "$lib/robot/context";
  import { readPosture, LOAD_TEXT } from "$lib/robot/posture";
  import {
    XrTeleopSession,
    checkXrSupport,
    type HandSample,
  } from "$lib/webxr/xr-teleop";
  import {
    buildFrame,
    connectTeleop,
    type TeleopFramePayload,
    type TeleopHandle,
    type TeleopState,
    type TeleopStatus,
  } from "$lib/teleop/stream";
  import {
    connectRobotCamera,
    type RobotCamHandle,
    type RobotCamState,
  } from "$lib/robot/mjpeg-camera";

  let { data } = $props();

  const live = getRobotLive();
  const posture = $derived(readPosture(live.state?.posture, live.online));
  const isAdmin = $derived(page.data.user?.role === "admin");
  const realHardware = $derived(data.env === "real");

  let overlayRoot = $state<HTMLDivElement>();

  // ---------------------------------------------------------------------
  // Combined locomotion dispatch.
  //
  // Both hold-to-walk and VR head-yaw turning ultimately drive the same
  // `walk_velocity` call (open-loop, real-hardware-only, hard-capped by the
  // bridge itself to 0.3 m/s / 0.3 rad/s / 3s per call — see
  // apps/bridge/src/bridge/skills/walk_velocity.py), so they share one
  // throttled loop rather than two independent timers that could each
  // overwrite the other's vx/vyaw on a real call — visible as the robot
  // zig-zagging between "walking straight" and "turning in place".
  //
  // NOT YET LIVE-TESTED on real hardware (bridge README, Phase 1b-workaround
  // entry), so every constant below stays well under the bridge's own cap
  // rather than pressing right up against it.
  // ---------------------------------------------------------------------
  const WALK_VX = 0.2; // m/s — below the bridge's 0.3 hard cap
  const STEP_S = 1.0; // seconds sustained per dispatched call
  const REPEAT_MS = STEP_S * 900; // re-issue slightly faster than STEP_S so
  // consecutive calls overlap a little rather than leaving a gap where the
  // firmware's own deadman (walk_velocity's duration) already zeroed it.
  const MAX_HOLD_S = 8; // dead-man ceiling for continuous combined motion.

  const YAW_DEADZONE_RAD = (6 * Math.PI) / 180; // ignore natural head wobble
  const YAW_FULL_SCALE_RAD = (30 * Math.PI) / 180; // head turn that saturates vyaw
  const YAW_MAX_RAD_S = 0.3; // below the bridge's 0.3 hard cap
  const VR_STALE_MS = 800; // no fresh pose sample in this long -> treat as 0

  type WalkDir = "forward" | "back";
  let walking = $state<WalkDir | null>(null);
  let walkError = $state<string | null>(null);
  let controlTimer: ReturnType<typeof setInterval> | null = null;
  // When the current continuous motion began, or null while stopped. Drives
  // the MAX_HOLD_S dead-man -- see `tick()`.
  let motionStartedAt: number | null = null;
  // Latched once the dead-man fires; blocks all motion until the operator
  // visibly lets go (head back to centre / fresh button press).
  let deadManTripped = false;

  let vrSupported = $state(false);
  let arSupported = $state(false);
  //: Which mode the live session actually got. Passthrough composites the DOM
  //: overlay; plain VR may not, which is what produced a black view with no
  //: controls in the first headset session.
  let xrMode = $state<string | null>(null);
  let handTrackingSupported = $state(false);
  let vrActive = $state(false);
  let vrError = $state<string | null>(null);
  let vr: XrTeleopSession | null = null;

  // ---------------------------------------------------------------------
  // Teleop stream — the arm-mirroring path.
  //
  // A second, deliberately separate transport: a WebSocket straight to
  // `apps/bridge/src/bridge/teleop/server.py` (port 8767), carrying head
  // yaw + both wrists + finger closure at ~30Hz. See that module and
  // `$lib/teleop/stream.ts` for why this does not go through apps/back.
  //
  // While it is open it becomes the ONLY commander of locomotion: the
  // `walk_velocity` loop below is suspended, and the walk buttons feed the
  // stream's `walk` axis instead. Two independent writers of SetVelocity
  // would silently fight, last-writer-wins, with neither aware of the other.
  // ---------------------------------------------------------------------
  let teleopState = $state<TeleopState>("closed");
  let teleopDetail = $state("");
  let teleopStatus = $state<TeleopStatus | null>(null);
  let teleop: TeleopHandle | null = null;
  let armsRequested = $state(false);
  const streaming = $derived(teleopState === "open");

  // Raw pose, updated at XR frame rate (~72-120Hz) -- kept as a plain
  // variable rather than $state so looking around doesn't force a Svelte
  // re-render every frame. `tick()` (900ms cadence) mirrors a rounded
  // snapshot into the two $state fields below for the UI readout.
  let yawErrorRadians = 0;
  let lastYawSampleAt = 0;
  let yawDisplayDeg = $state(0);
  let trackingStale = $state(false);
  // Head position and both wrists, at XR frame rate. Plain variables for the
  // same reason as `yawErrorRadians`: these change 72-120 times a second and
  // nothing renders them directly.
  let headPosition: [number, number, number] = [0, 1.6, 0];
  let handLeft: HandSample | null = null;
  let handRight: HandSample | null = null;
  let handsVisible = $state(false);

  onMount(() => {
    void checkXrSupport().then((s) => {
      vrSupported = s.immersiveVr || s.immersiveAr;
      arSupported = s.immersiveAr;
      handTrackingSupported = s.handTracking;
    });
  });

  function computeVyaw(): number {
    if (!vrActive || deadManTripped) return 0;
    if (Date.now() - lastYawSampleAt > VR_STALE_MS) return 0; // tracking stale
    const err = yawErrorRadians;
    const mag = Math.abs(err);
    if (mag < YAW_DEADZONE_RAD) return 0;
    const scaled =
      ((mag - YAW_DEADZONE_RAD) / (YAW_FULL_SCALE_RAD - YAW_DEADZONE_RAD)) *
      YAW_MAX_RAD_S;
    return Math.sign(err) * Math.min(YAW_MAX_RAD_S, scaled);
  }

  async function sendVelocity(vx: number, vyaw: number, duration_s: number) {
    const { error } = await createApi(fetch)
      .skills({ name: "walk_velocity" })
      .invoke.post({ vx, vy: 0, vyaw, duration_s });
    if (error) {
      const status = error.status as number;
      if (status === 401) {
        if (browser) await goto("/login");
        return;
      }
      walkError =
        status === 403
          ? "Se requiere una cuenta admin para mover al robot."
          : "No se pudo enviar el comando de movimiento.";
      walking = null;
      stopLoop();
    }
  }

  function loopShouldRun(): boolean {
    // While the stream is open the bridge owns locomotion. Running this loop
    // too would put two writers on SetVelocity.
    if (streaming) return false;
    return walking !== null || vrActive;
  }

  function stopLoop() {
    if (controlTimer) clearInterval(controlTimer);
    controlTimer = null;
  }

  function ensureLoopRunning() {
    if (controlTimer || !loopShouldRun()) return;
    controlTimer = setInterval(tick, REPEAT_MS);
  }

  /**
   * One dispatch tick.
   *
   * The dead-man measures time since MOTION began, not since the loop or the
   * VR session began, and latches when it fires. An earlier version keyed off
   * loop start and re-armed itself every time it tripped, which broke it in
   * both directions: entering VR and then pressing walk 7s later gave a
   * one-second ceiling instead of eight, while a continuously-held turn just
   * got a fresh 8s window forever -- so it never actually stopped anything,
   * it only injected a stop pulse every 8s (and kept injecting them while
   * idle, with the head at centre and no button held).
   */
  function tick() {
    yawDisplayDeg = Math.round((yawErrorRadians * 180) / Math.PI);
    trackingStale = vrActive && Date.now() - lastYawSampleAt > VR_STALE_MS;

    if (!loopShouldRun()) {
      stopLoop();
      return;
    }

    const wantVx =
      walking === "forward" ? WALK_VX : walking === "back" ? -WALK_VX : 0;
    const wantVyaw = computeVyaw(); // already 0 while the dead-man is tripped

    // Re-arm once the operator has visibly let go: no walk button held AND
    // the head back near centre. A fresh button press re-arms too, via
    // startWalking -- both are deliberate human actions, which is the point.
    if (
      deadManTripped &&
      wantVx === 0 &&
      Math.abs(yawErrorRadians) < YAW_DEADZONE_RAD
    ) {
      deadManTripped = false;
    }
    if (deadManTripped) {
      motionStartedAt = null;
      return;
    }

    if (wantVx === 0 && wantVyaw === 0) {
      // Nothing requested. If we were moving, send one explicit stop rather
      // than relying solely on the firmware's own duration deadman.
      if (motionStartedAt !== null) {
        motionStartedAt = null;
        void sendVelocity(0, 0, 0.5);
      }
      return;
    }

    if (motionStartedAt === null) {
      motionStartedAt = Date.now();
    } else if (Date.now() - motionStartedAt > MAX_HOLD_S * 1000) {
      deadManTripped = true;
      motionStartedAt = null;
      walking = null;
      void sendVelocity(0, 0, 0.5);
      return;
    }

    void sendVelocity(wantVx, wantVyaw, STEP_S);
  }

  function startWalking(dir: WalkDir) {
    if (walking) return;
    walking = dir;
    walkError = null;
    // A deliberate fresh press is the "release and re-press" gesture that
    // re-arms the dead-man, and starts a full new motion window.
    deadManTripped = false;
    motionStartedAt = null;
    if (streaming) return;
    ensureLoopRunning();
    tick(); // immediate feedback, don't wait for the next interval tick
  }

  function stopWalking() {
    if (!walking) return;
    walking = null;
    if (streaming) return;
    if (!loopShouldRun()) {
      stopLoop();
      void sendVelocity(0, 0, 0.5);
    }
    // else: VR turning is still active -- the running loop already accounts
    // for walking being cleared on its next tick.
  }

  // --- VR head-yaw ---------------------------------------------------

  async function enterVr() {
    vrError = null;
    if (!overlayRoot) return;
    const session = new XrTeleopSession({
      onSample: (s) => {
        yawErrorRadians = s.yawErrorRadians;
        headPosition = s.headPosition;
        handLeft = s.left;
        handRight = s.right;
        lastYawSampleAt = Date.now();
        noteHandsVisible();
      },
      onEnd: () => {
        vrActive = false;
        xrMode = null;
        vr = null;
        // Leaving VR must not leave the arms up: the stream's own frames stop
        // carrying `arms: true`, which is the bridge's cue to ramp the
        // arm_sdk weight back down.
        // The next pulled frame reports `fresh: false` on its own, so there is
        // nothing to push here — that is the point of pulling.
        armsRequested = false;
        handLeft = null;
        handRight = null;
        if (!loopShouldRun()) {
          stopLoop();
          void sendVelocity(0, 0, 0.5);
        }
      },
    });
    try {
      await session.start(overlayRoot);
      vr = session;
      xrMode = session.mode;
      vrActive = true;
      deadManTripped = false;
      motionStartedAt = null;
      ensureLoopRunning();
    } catch (err) {
      vrError =
        err instanceof Error ? err.message : "No se pudo iniciar la sesión VR.";
    }
  }

  function exitVr() {
    vr?.stop();
  }

  function recenterVr() {
    // Recentring makes yawError ~0, which is itself the re-arm condition --
    // clearing the latch here just avoids waiting a tick for it.
    vr?.recenter();
    deadManTripped = false;
    motionStartedAt = null;
  }

  // --- Teleop stream ---------------------------------------------------

  /**
   * Build the frame the sender is about to transmit. Pulled ~30 times a
   * second by `$lib/teleop/stream.ts`, never cached — see `buildFrame`, which
   * holds the freshness policy and its tests.
   */
  function buildTeleopFrame(): TeleopFramePayload {
    return buildFrame({
      now: Date.now(),
      vrActive,
      lastSampleAt: lastYawSampleAt,
      staleAfterMs: VR_STALE_MS,
      yawErrorRadians,
      yawDeadzoneRadians: YAW_DEADZONE_RAD,
      headPosition,
      left: handLeft,
      right: handRight,
      walking,
      armsRequested,
    });
  }

  /** Mirror hand visibility into the UI, cheaply. Called from the XR loop. */
  function noteHandsVisible() {
    // Guarded assignment: this runs at XR frame rate, and writing a $state
    // field unconditionally would queue a Svelte re-render 72-120 times a
    // second for a boolean that changes twice a session.
    const seen = handLeft !== null || handRight !== null;
    if (seen !== handsVisible) handsVisible = seen;
  }

  function connectStream() {
    teleop?.close();
    teleopStatus = null;
    const host = env.PUBLIC_TELEOP_HOST || location.hostname;
    const port = env.PUBLIC_TELEOP_PORT || "8767";
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    teleop = connectTeleop(`${protocol}://${host}:${port}`, {
      getFrame: buildTeleopFrame,
      onState: (state, detail) => {
        teleopState = state;
        teleopDetail = detail ?? "";
        if (state === "open") {
          // The bridge is the commander now; make sure this page is not also
          // mid-flight with a walk_velocity call.
          stopLoop();
          void sendVelocity(0, 0, 0.5);
        } else {
          armsRequested = false;
          ensureLoopRunning();
        }
      },
      onStatus: (status) => (teleopStatus = status),
    });
  }

  function disconnectStream() {
    armsRequested = false;
    teleop?.close();
    teleop = null;
    teleopState = "closed";
    teleopStatus = null;
  }

  function toggleArms() {
    armsRequested = !armsRequested;
  }

  // --- Camera mirror ---------------------------------------------------
  //
  // Same source as /live-camera: `apps/perception`'s vision container, which
  // is the process that already owns the D435i — and a V4L2 device has exactly
  // one owner, so it is the only thing that CAN show this picture.
  //
  // This page previously used its own WebSocket relay over teleimager's ZeroMQ
  // feed. That path is gone: teleimager is not running on the robot, and the
  // `/dev/video4` it opened no longer exists (colour moved to video5). Two
  // camera transports where one cannot work is worse than one that does.
  //
  // The status poll is not decoration. An <img> pointed at an MJPEG stream
  // keeps showing its last frame forever, with no event — so a camera that
  // died two minutes ago looks identical to a working one. The picture comes
  // from the <img>; whether it is LIVE comes from /status.
  let camState = $state<RobotCamState>("closed");
  let camDetail = $state("");
  let camFrameUrl = $state<string | null>(null);
  let camLive = $state(false);
  let camHandle: RobotCamHandle | null = null;

  function connectCamera() {
    camHandle?.close();
    const base = (env.PUBLIC_ROBOT_CAM_URL ?? "").trim();
    if (!base) {
      camState = "error";
      camDetail = "PUBLIC_ROBOT_CAM_URL no está configurado.";
      return;
    }
    camHandle = connectRobotCamera(base, {
      onState: (state, detail) => {
        camState = state;
        camDetail = detail ?? "";
      },
      onStatus: (status) => {
        camLive = status?.live ?? false;
      },
      onStreamUrl: (url) => (camFrameUrl = url),
    });
  }

  onMount(() => {
    if (realHardware) connectCamera();
    return () => camHandle?.close();
  });

  onDestroy(() => {
    // SSR runs component setup and teardown on the server, so this fired
    // there — and its safety stop called `goto("/login")` on a 401, which
    // throws "Cannot call goto(...) on the server" and took the whole dev
    // server down with it. The page had never been rendered before the first
    // headset session, which is exactly when it surfaced.
    //
    // Nothing in this teardown means anything off the client: there is no
    // robot to stop from a render process.
    if (!browser) return;
    walking = null;
    armsRequested = false;
    vr?.stop();
    // Closing the stream sends one last released frame, so the bridge stops on
    // a frame rather than waiting out its staleness timeout.
    teleop?.close();
    teleop = null;
    stopLoop();
    void sendVelocity(0, 0, 0.5);
  });

  // ---------------------------------------------------------------------
  // Presets: one-shot gestures, same request shape stop-button.svelte uses.
  // `dance` is new (apps/bridge/src/bridge/skills/dance.py) and, like
  // walk_velocity, is not yet live-tested — the catalogue's `works.real`
  // flag drives the badge below rather than hiding either button.
  // ---------------------------------------------------------------------
  const presets = [
    { name: "wave", label: "Saludar", icon: Hand },
    { name: "dance", label: "Bailar", icon: Music },
    { name: "shake_hand", label: "Dar la mano", icon: Handshake },
    { name: "hug", label: "Abrazar", icon: HeartHandshake },
    { name: "clap", label: "Aplaudir", icon: PartyPopper },
    { name: "release_arm", label: "Relajar brazos", icon: RotateCcw },
  ] as const;

  // ---------------------------------------------------------------------
  // Bring-up.
  //
  // Without this the page was unusable on hardware and the reason was
  // invisible. Walking needs the robot in a walk program, and `rt/arm_sdk`
  // needs FSM 4 / 500 / 501 — but /vr-control only ever *displayed* posture,
  // it could not change it. So an operator already wearing the headset would
  // press Adelante, get nothing, enable the arm mirror, get "FSM ... is not
  // one of [4, 500, 501]", and have to take the headset off to fix it
  // somewhere else.
  //
  // The order is the one that actually worked on this robot (2026-08-15):
  // damp -> prepare (4) -> start_walking_waist (501). **501, not 500** — the
  // two are walk programs selected by waist DoF and this body reports
  // mode_machine 5, the 29-DoF/3-DoF-waist variant. 500 was the blocker for
  // two whole sessions of Start() returning success and doing nothing.
  const bringUp = [
    {
      name: "damp",
      label: "1 · Amortiguar",
      hint: "Punto de partida seguro. El robot se sostiene blando.",
    },
    {
      name: "prepare",
      label: "2 · Preparar",
      hint: "FSM 4. Habilita el espejo de brazos.",
    },
    {
      name: "start_walking_waist",
      label: "3 · Habilitar marcha",
      hint: "FSM 501 — el programa de marcha de ESTE cuerpo. Habilita caminar y girar.",
    },
  ] as const;

  // "denied" is separated from "error" because they need different actions
  // from the operator. A 403 is not a robot problem — the skill exists, the
  // robot is fine, this account simply is not admin — and telling somebody in
  // a headset that a gesture "Falló" sends them to debug the robot instead of
  // the account.
  type PresetOutcome = "ok" | "error" | "denied" | null;
  let presetBusy = $state<string | null>(null);
  let presetOutcome = $state<Record<string, PresetOutcome>>({});

  async function runPreset(name: string) {
    if (presetBusy) return;
    presetBusy = name;
    presetOutcome = { ...presetOutcome, [name]: null };
    try {
      const { error } = await createApi(fetch).skills({ name }).invoke.post({});
      const status = error ? (error.status as number) : 0;
      if (status === 401) {
        if (browser) await goto("/login");
        return;
      }
      presetOutcome = {
        ...presetOutcome,
        [name]: !error ? "ok" : status === 403 ? "denied" : "error",
      };
    } catch {
      presetOutcome = { ...presetOutcome, [name]: "error" };
    } finally {
      presetBusy = null;
    }
  }
</script>

<svelte:head><title>Control VR · C3PO</title></svelte:head>

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
    set of semantics is worth more than two that might drift.
  -->
  <div
    class="sticky top-0 z-20 -mx-1 flex items-center justify-between gap-3 border-b border-hairline bg-background/95 px-1 py-2 backdrop-blur"
  >
    <span class="eyebrow">Parada de emergencia</span>
    <StopButton />
  </div>

  <header class="flex flex-wrap items-center justify-between gap-3">
    <div>
      <h1 class="stamp-quiet text-xl text-ink">Control VR</h1>
      <p class="text-sm text-ink-mute">
        Movimiento y gestos preprogramados — pensado para abrirse desde el
        navegador del Quest.
      </p>
    </div>
    <div class="flex items-center gap-2">
      <span class="eyebrow">Postura</span>
      <span class={`text-sm font-medium ${LOAD_TEXT[posture.load]}`}
        >{posture.label}</span
      >
    </div>
  </header>

  {#if !realHardware}
    <div
      class="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
    >
      <TriangleAlert class="mt-0.5 size-4 shrink-0 text-warn" />
      <p>
        El bridge no está en modo real (env: {data.env ?? "desconocido"}).
        Caminar y bailar solo funcionan contra hardware real — acá el robot no
        se va a mover.
      </p>
    </div>
  {/if}

  {#if !isAdmin}
    <div
      class="flex items-start gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 text-sm text-ink"
    >
      <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger-soft" />
      <p>
        Esta cuenta no es admin. <strong
          >Los gestos y la puesta en marcha van a fallar con 403</strong
        > — invocar skills directamente requiere rol admin. Caminar y girar con la
        cabeza SÍ funcionan: van por el stream de teleoperación, que no pasa por esa
        puerta. PARAR también funciona siempre, porque está clasificado como skill
        de seguridad.
      </p>
    </div>
  {/if}

  {#if realHardware}
    <section class="flex flex-col gap-3 panel p-5">
      <div class="flex items-center justify-between gap-2">
        <span class="eyebrow"
          >Cámara{camFrameUrl && !camLive ? " · congelada" : ""}</span
        >
        <Button
          variant="outline"
          size="sm"
          onclick={connectCamera}
          class="h-auto gap-1.5 rounded-full px-3 py-1 text-2xs"
        >
          <RotateCw class="size-3.5" /> Reconectar
        </Button>
      </div>
      <div
        class="flex aspect-video items-center justify-center overflow-hidden rounded-lg bg-trench"
      >
        {#if camFrameUrl && (camState === "live" || camState === "stale")}
          <img
            src={camFrameUrl}
            alt="Cámara del robot"
            class="h-full w-full object-contain"
            class:opacity-40={!camLive}
          />
        {:else}
          <div class="flex flex-col items-center gap-2 text-ink-mute">
            {#if camState === "connecting"}
              <Video class="size-6 animate-pulse" />
              <span class="text-sm">Conectando…</span>
            {:else}
              <VideoOff class="size-6" />
              <span class="text-sm"
                >Sin señal{camDetail ? ` — ${camDetail}` : ""}</span
              >
            {/if}
          </div>
        {/if}
      </div>
    </section>
  {/if}

  <section class="flex flex-col gap-4 panel p-5">
    <span class="eyebrow">Puesta en marcha</span>
    <p class="readout">
      Estado actual:
      <strong class={LOAD_TEXT[posture.load]}>{posture.label}</strong>
      — {posture.detail}
    </p>
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {#each bringUp as step (step.name)}
        <button
          type="button"
          class="flex min-h-20 flex-col items-start justify-center gap-1 tile-interactive p-3 text-left text-sm text-ink disabled:opacity-60"
          disabled={presetBusy === step.name}
          onclick={() => runPreset(step.name)}
        >
          <span class="font-display font-semibold">{step.label}</span>
          <span class="text-xs text-ink-mute">{step.hint}</span>
          {#if presetOutcome[step.name] === "denied"}
            <span class="text-xs text-warn">Requiere admin</span>
          {:else if presetOutcome[step.name] === "error"}
            <span class="text-xs text-danger-soft">Falló</span>
          {:else if presetOutcome[step.name] === "ok"}
            <span class="text-xs text-cyan">Enviado</span>
          {/if}
        </button>
      {/each}
    </div>
    <p class="readout">
      En este orden. Caminar y girar necesitan el paso 3; el espejo de brazos
      necesita el 2 o el 3. Si algo se ignora en silencio, casi siempre es
      porque el robot no está en ninguno de esos estados.
    </p>
  </section>

  <section class="flex flex-col gap-4 panel p-5">
    <span class="eyebrow">Caminar</span>
    <div class="grid grid-cols-2 gap-4">
      <button
        type="button"
        class="flex min-h-28 touch-none flex-col items-center justify-center gap-2 rounded-lg border border-hairline bg-wash font-display text-lg font-semibold text-ink transition-colors duration-100 select-none data-active:border-cyan data-active:bg-cyan/15 data-active:text-cyan"
        data-active={walking === "forward"}
        onpointerdown={() => startWalking("forward")}
        onpointerup={stopWalking}
        onpointerleave={stopWalking}
        onpointercancel={stopWalking}
      >
        <ArrowUp class="size-7" />
        Adelante
      </button>
      <button
        type="button"
        class="flex min-h-28 touch-none flex-col items-center justify-center gap-2 rounded-lg border border-hairline bg-wash font-display text-lg font-semibold text-ink transition-colors duration-100 select-none data-active:border-cyan data-active:bg-cyan/15 data-active:text-cyan"
        data-active={walking === "back"}
        onpointerdown={() => startWalking("back")}
        onpointerup={stopWalking}
        onpointerleave={stopWalking}
        onpointercancel={stopWalking}
      >
        <ArrowDown class="size-7" />
        Atrás
      </button>
    </div>
    <p class="readout">
      {#if walking}
        Manteniendo {WALK_VX} m/s {walking === "forward"
          ? "adelante"
          : "atrás"}…
      {:else}
        Mantené presionado para caminar. Al soltar, frena solo.
      {/if}
    </p>
    {#if walkError}
      <p role="alert" class="text-sm text-danger-soft">{walkError}</p>
    {/if}
  </section>

  <section class="flex flex-col gap-4 panel p-5">
    <span class="eyebrow">Girar con la cabeza (VR)</span>
    {#if !vrSupported}
      <p class="text-sm text-ink-mute">
        WebXR no está disponible en este navegador. Abrí esta página desde el
        navegador del Quest para usar esta función.
      </p>
    {:else if !vrActive}
      <Button onclick={enterVr} class="w-fit gap-2 cta">
        <Glasses class="size-4" /> Entrar en VR
      </Button>
    {:else}
      <div class="flex flex-wrap items-center gap-3">
        <Button variant="outline" onclick={recenterVr}>Recentrar</Button>
        <Button variant="outline" onclick={exitVr}>Salir de VR</Button>
        <span class="readout">
          {xrMode === "immersive-ar" ? "Passthrough · " : ""}{yawDisplayDeg}° de
          giro{trackingStale ? " · seguimiento perdido" : ""}
        </span>
      </div>
    {/if}
    <p class="readout">
      Girá la cabeza más de 6° respecto del frente calibrado para que el robot
      empiece a girar; a más ángulo, más rápido, hasta 30°. Se puede combinar
      con los botones de caminar.
    </p>
    {#if vrActive && xrMode !== "immersive-ar"}
      <p
        role="alert"
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>Sesión sin passthrough.</strong> El visor no dio
        <code>immersive-ar</code>, así que puede que no veas nada más que negro:
        el overlay del navegador no se compone en modo VR puro. Los controles
        siguen funcionando a ciegas, pero salí de VR si necesitás verlos.
      </p>
    {/if}

    {#if !arSupported && vrSupported}
      <p class="text-sm text-ink-mute">
        Este visor no reporta passthrough (<code>immersive-ar</code>). Vas a
        entrar en VR puro, donde el overlay puede no verse.
      </p>
    {/if}

    {#if vrError}
      <p role="alert" class="text-sm text-danger-soft">{vrError}</p>
    {/if}
  </section>

  <section class="flex flex-col gap-4 panel p-5">
    <span class="eyebrow">Espejo de brazos (teleoperación)</span>

    <div class="flex flex-wrap items-center gap-3">
      {#if !streaming}
        <Button onclick={connectStream} class="w-fit gap-2 cta">
          <Link2 class="size-4" /> Conectar puente
        </Button>
      {:else}
        <Button variant="outline" onclick={disconnectStream} class="gap-2">
          <Link2Off class="size-4" /> Desconectar
        </Button>
        <Button
          onclick={toggleArms}
          disabled={!vrActive}
          class={armsRequested ? "gap-2 cta" : "gap-2"}
          variant={armsRequested ? "default" : "outline"}
        >
          <Hand class="size-4" />
          {armsRequested ? "Espejo activo" : "Activar espejo"}
        </Button>
      {/if}
      <span class="readout">
        {#if teleopState === "connecting"}
          Conectando…
        {:else if streaming}
          Conectado{handsVisible ? " · manos detectadas" : ""}
        {:else if teleopState === "error" || teleopDetail}
          {teleopDetail || "Sin conexión"}
        {:else}
          Sin conexión
        {/if}
      </span>
    </div>

    {#if streaming}
      <dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
        <div>
          <dt class="eyebrow">Frames</dt>
          <dd class="readout">{teleopStatus?.frames_received ?? 0}</dd>
        </div>
        <div>
          <dt class="eyebrow">Calibración</dt>
          <dd class="readout">
            {teleopStatus?.calibrated
              ? `${teleopStatus.arm_length_m} m`
              : "pendiente"}
          </dd>
        </div>
        <div>
          <dt class="eyebrow">Brazos</dt>
          <dd class="readout">
            {teleopStatus?.arm.engaged
              ? `peso ${teleopStatus.arm.weight}`
              : "sueltos"}
          </dd>
        </div>
        <div>
          <dt class="eyebrow">Manos</dt>
          <dd class="readout">{teleopStatus?.hands ?? "—"}</dd>
        </div>
      </dl>
    {/if}

    {#if teleopStatus?.stopped_by_estop}
      <p
        role="alert"
        class="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>Detenido por PARAR.</strong> El puente cortó el movimiento. Soltá
        los controles (y bajá la cabeza al centro) para volver a habilitarlo — se
        mantiene detenido mientras sigas apretando.
      </p>
    {/if}

    {#if teleopStatus?.deadman_tripped}
      <p role="alert" class="text-sm text-warn">
        Hombre muerto activado: se mantuvo el movimiento demasiado tiempo. Soltá
        todo y volvé a empezar.
      </p>
    {/if}

    {#if streaming && armsRequested && teleopStatus?.arm_error}
      <p
        role="alert"
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        El puente no tomó los brazos: {teleopStatus.arm_error}
      </p>
    {/if}

    {#if !handTrackingSupported}
      <p class="text-sm text-ink-mute">
        Este navegador no expone seguimiento de manos. En el Quest hay que
        habilitarlo en Ajustes → Movimiento.
      </p>
    {/if}

    <p class="readout">
      Los brazos del robot copian la dirección y la extensión de los tuyos — no
      la posición exacta en metros, porque el puente no tiene el URDF del G1
      (ver <code>bridge/teleop/retarget.py</code>). El camino
      <code>rt/arm_sdk</code> viene <strong>deshabilitado</strong>: hay que
      verificar los signos de cada articulación con
      <code>scripts/arm_sign_check.py</code>, al lado del robot, antes de poner
      <code>TELEOP_ARM_ENABLED=1</code>. Los dedos siguen apagados hasta
      resolver qué manos tiene puestas este robot (<code
        >scripts/hand_probe.py</code
      >).
    </p>
  </section>

  <section class="flex flex-col gap-4 panel p-5">
    <span class="eyebrow">Gestos preprogramados</span>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {#each presets as preset (preset.name)}
        {@const Icon = preset.icon}
        {@const untested = data.worksReal[preset.name] === false}
        <button
          type="button"
          class="flex min-h-24 flex-col items-center justify-center gap-1.5 tile-interactive p-3 text-center text-sm text-ink disabled:opacity-60"
          disabled={presetBusy === preset.name}
          onclick={() => runPreset(preset.name)}
        >
          <Icon class="size-5" />
          <span>{preset.label}</span>
          {#if untested}
            <Badge variant="outline" class="text-2xs">Sin probar en real</Badge>
          {/if}
          {#if presetOutcome[preset.name] === "denied"}
            <span class="text-xs text-warn">Requiere admin</span>
          {:else if presetOutcome[preset.name] === "error"}
            <span class="text-xs text-danger-soft">Falló</span>
          {/if}
        </button>
      {/each}
    </div>
  </section>
</div>
