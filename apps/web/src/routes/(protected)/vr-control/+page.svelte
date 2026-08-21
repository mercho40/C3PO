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
  import { goto, invalidateAll } from "$app/navigation";
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
  import { authClient } from "$lib/auth-client";
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

  /**
   * Release every held control, from anywhere.
   *
   * The walk buttons are released only by their own pointer events. That is
   * fine until something removes the button without delivering one: the system
   * "leave immersive" gesture, the headset coming off, the dom-overlay being
   * torn down. `walking` then stays latched — and `buildFrame` derives `walk`
   * and half of `enabled` from it with no freshness gate (deliberately: the
   * buttons must keep working with no headset at all), so the page transmits
   * "walk forward" 30 times a second, indefinitely, with nothing to stop it.
   *
   * The page's own MAX_HOLD_S dead-man cannot catch it either, because that
   * loop is suspended while streaming. So: a window-level release, wired to
   * every event that means "the operator is no longer holding this".
   */
  function releaseAllControls() {
    if (walking !== null) {
      walking = null;
      if (!bridgeHolds && !loopShouldRun()) {
        stopLoop();
        void sendVelocity(0, 0, 0.5);
      }
    }
  }

  const live = getRobotLive();
  const posture = $derived(readPosture(live.state?.posture, live.online));
  const isAdmin = $derived(page.data.user?.role === "admin");

  // Better Auth caches the session — role included — in a `better-auth.session_data`
  // cookie for five minutes. So an account promoted to admin keeps reading as
  // "not admin" until that expires, and the page has no way to know the
  // difference between "you are not an admin" and "you were made one four
  // minutes ago". The operator sees a hard permission error for something that
  // is already fixed, in a headset, with no terminal.
  //
  // `disableCookieCache` asks the server directly, skipping the cookie; the
  // reply refreshes it. `invalidateAll` then re-runs the load functions so
  // `page.data.user` — which is what `isAdmin` reads — picks the new role up.
  let refreshingRole = $state(false);
  let roleRefreshOutcome = $state<string | null>(null);

  async function refreshRole() {
    if (refreshingRole) return;
    refreshingRole = true;
    roleRefreshOutcome = null;
    try {
      const { data, error } = await authClient.getSession({
        query: { disableCookieCache: true },
      });
      if (error) {
        roleRefreshOutcome = `No se pudo verificar: ${error.message ?? "error de sesión"}`;
        return;
      }
      await invalidateAll();
      roleRefreshOutcome =
        data?.user?.role === "admin"
          ? "Listo — la cuenta es admin."
          : "El servidor sigue diciendo que esta cuenta no es admin. Hay que agregarla a ADMIN_EMAILS y reiniciar el backend.";
    } catch (err) {
      roleRefreshOutcome = `No se pudo verificar: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      refreshingRole = false;
    }
  }
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
  //: Whether the in-headset camera layer has decoded a frame. Distinct from
  //: the on-page panel: they share a source but not a decoder, so one can be
  //: live while the other is not.
  let xrCameraLive = $state(false);
  //: Has the headset layer EVER decoded a frame? Distinct from `xrCameraLive`,
  //: which is a liveness flag. Conflating them made the page tell the operator
  //: "no frame ever arrived, perception is probably not running" during an
  //: ordinary one-second stall — a confident, wrong diagnosis pointing them at
  //: the robot while a real picture was on screen in front of them.
  let xrCameraEverHadFrame = $state(false);
  //: The XR layer dropped the picture because drawing threw — a shader compile
  //: or link failure, not anything to do with the robot. The session sets this
  //: and nothing read it, so a GL failure was reported to the operator as
  //: "perception is probably not running": the worst diagnosis attached to the
  //: least likely cause.
  let xrCameraBroken = $state(false);

  // --- USB link watch ---------------------------------------------------
  //
  // `adb reverse` forwards live on the USB connection, not the headset. Unplug
  // the cable, jostle it, or let the device drop its ADB session and ALL of
  // them vanish at once — and nothing recreates them. From inside the headset
  // that is indistinguishable from the server being down, which is exactly how
  // it presented on 2026-08-20: "localhost is not working", with every service
  // on the Mac perfectly healthy.
  //
  // Polling our own origin is enough to tell them apart, because the page was
  // served through the same forward that would have died.
  let linkLost = $state(false);
  let linkTimer: ReturnType<typeof setInterval> | null = null;

  function startLinkWatch() {
    if (linkTimer) return;
    linkTimer = setInterval(async () => {
      try {
        await fetch(`/vr-control?ping=${Date.now()}`, {
          method: "HEAD",
          cache: "no-store",
          signal: AbortSignal.timeout(4000),
        });
        linkLost = false;
      } catch {
        linkLost = true;
      }
    }, 5000);
  }
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
  //: The bridge holds locomotion whenever the socket is up — INCLUDING while
  //: stalled. `streaming` goes false there, which used to re-open the door for
  //: this page's own velocity loop: the socket is still OPEN, its 33 Hz sender
  //: is still transmitting, and the moment the bridge starts reading again
  //: both writers are live on SetVelocity. `stalled` is a diagnosis, not a
  //: handover.
  const bridgeHolds = $derived(
    teleopState === "open" || teleopState === "stalled",
  );

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
    // `pointerup` alone is not enough: a pointer can be cancelled, the window
    // can lose focus, or the page can be hidden by the system UI, none of
    // which fire it on the button.
    const release = () => releaseAllControls();
    const onHidden = () => {
      if (document.visibilityState === "hidden") releaseAllControls();
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", release);
    document.addEventListener("visibilitychange", onHidden);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", onHidden);
    };
  });

  onMount(() => {
    startLinkWatch();
    return () => {
      if (linkTimer) clearInterval(linkTimer);
      linkTimer = null;
    };
  });

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
    // While the socket is up the bridge owns locomotion. Running this loop too
    // would put two writers on SetVelocity. `bridgeHolds`, not `streaming`:
    // a stalled socket is still transmitting.
    if (bridgeHolds) return false;
    return walking !== null || vrActive;
  }

  function stopLoop() {
    if (controlTimer) clearInterval(controlTimer);
    controlTimer = null;
  }

  // --- UI readouts -----------------------------------------------------
  //
  // These used to be written only by `tick()`, which is the LOCOMOTION loop —
  // and that loop is deliberately suspended while the teleop stream is
  // connected, because the bridge is the commander then. So during the normal
  // operating mode the "N deg de giro" readout froze at a stale number and
  // "seguimiento perdido" could never appear: the operator's only in-headset
  // indication that the pose driving the robot had gone stale was dead
  // precisely when it mattered.
  //
  // Readouts are not dispatch. They run whenever there is a session to read.
  let uiTimer: ReturnType<typeof setInterval> | null = null;

  function refreshReadouts() {
    yawDisplayDeg = Math.round((yawErrorRadians * 180) / Math.PI);
    trackingStale = vrActive && Date.now() - lastYawSampleAt > VR_STALE_MS;
    if (vr) {
      xrCameraLive = camLive && vr.cameraHasFrame;
      xrCameraEverHadFrame ||= vr.cameraHasFrame;
      xrCameraBroken = vr.cameraBroken;
    }
  }

  function ensureReadouts() {
    if (uiTimer) return;
    uiTimer = setInterval(refreshReadouts, 200);
  }

  function stopReadouts() {
    if (uiTimer) clearInterval(uiTimer);
    uiTimer = null;
    trackingStale = false;
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
    if (vr) {
      xrCameraLive = camLive && vr.cameraHasFrame;
      xrCameraEverHadFrame ||= vr.cameraHasFrame;
      xrCameraBroken = vr.cameraBroken;
    }
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
    // `bridgeHolds`, not `streaming`: a stalled socket is still open and still
    // transmitting, so starting this loop would make two writers.
    if (bridgeHolds) return;
    ensureLoopRunning();
    tick(); // immediate feedback, don't wait for the next interval tick
  }

  function stopWalking() {
    if (!walking) return;
    walking = null;
    if (bridgeHolds) return;
    if (!loopShouldRun()) {
      stopLoop();
      void sendVelocity(0, 0, 0.5);
    }
    // else: VR turning is still active -- the running loop already accounts
    // for walking being cleared on its next tick.
  }

  // --- VR head-yaw ---------------------------------------------------

  //: Guards `enterVr` itself. The session object has its own re-entrancy
  //: guard, but it is PER INSTANCE and this function builds a new instance on
  //: every call — so two taps made two sessions and the guard never fired.
  //: `vrActive` is no use either: it is set after several awaits, including
  //: one that blocks on the headset's own consent prompt.
  let enteringVr = false;

  async function enterVr() {
    if (enteringVr) return;
    enteringVr = true;
    try {
      await startVrSession();
    } finally {
      enteringVr = false;
    }
  }

  async function startVrSession() {
    vrError = null;
    if (!overlayRoot) return;
    // The headset shares the page's ONE camera connection rather than opening
    // its own: that connection already polls /status, tracks live/stale and
    // reconnects with a fresh URL, which the vision server requires because it
    // CLOSES the stream whenever it goes stale. Two independent connections
    // would also mean two MJPEG streams over the same SSH tunnel.
    const camBase = (env.PUBLIC_ROBOT_CAM_URL ?? "").trim();
    const session = new XrTeleopSession(
      {
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
          xrCameraLive = false;
          xrCameraEverHadFrame = false;
          xrCameraBroken = false;
          vr = null;
          // A held walk button does NOT survive the session that was showing
          // it. Nothing else clears `walking` here: the window listeners cover
          // pointerup/cancel/blur/visibilitychange, and session end fires none
          // of them reliably — the document becomes *visible* when an
          // immersive session ends, not hidden.
          //
          // Without this, taking the headset off mid-stride left `walking` set
          // and `loopShouldRun()` true, so the page kept dispatching
          // walk_velocity every 900 ms to a robot nobody was watching, until
          // the 8 s hold latch caught it. That is about 1.6 m of unattended
          // walking.
          releaseAllControls();
          // Leaving VR must not leave the arms up: the stream's own frames stop
          // carrying `arms: true`, which is the bridge's cue to ramp the
          // arm_sdk weight back down.
          // The next pulled frame reports `fresh: false` on its own, so there is
          // nothing to push here — that is the point of pulling.
          armsRequested = false;
          handLeft = null;
          handRight = null;
          // The readout timer is started on VR entry and was never stopped:
          // `stopReadouts()` had no callers at all, so a 200 ms interval
          // outlived the session and the component, writing $state on a
          // destroyed page for as long as the tab lived.
          stopReadouts();
          if (!loopShouldRun()) {
            stopLoop();
            void sendVelocity(0, 0, 0.5);
          }
        },
      },
      { camera: camBase !== "" },
    );
    try {
      await session.start(overlayRoot);
      vr = session;
      // The camera connection may already be running — hand over its current
      // URL and liveness so entering VR after the feed is up shows a picture
      // immediately rather than waiting for the next reconnect.
      if (camFrameUrl) session.setCameraStream(camFrameUrl);
      session.setCameraLive(camLive);
      xrMode = session.mode;
      vrActive = true;
      ensureReadouts();
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
          return;
        }
        armsRequested = false;
        if (state === "stalled") {
          // Deliberately NOT starting the local loop here. The socket is still
          // open, so the bridge may start reading again at any moment — and
          // two writers on SetVelocity is a worse failure than one that has
          // stopped. The bridge's own 0.4 s staleness watchdog has already
          // brought the robot to rest, so nothing is running away while the
          // operator decides. The status line says to reconnect.
          return;
        }
        ensureLoopRunning();
      },
      onStatus: (status) => (teleopStatus = status),
    });
  }

  function disconnectStream() {
    armsRequested = false;
    teleop?.close();
    teleop = null;
    teleopState = "closed";
    // Cleared too: the status line renders on `teleopDetail` being truthy, so
    // a leftover stall or "session already active" message kept showing after
    // a deliberate disconnect, as though it were current.
    teleopDetail = "";
    teleopStatus = null;
    // `close()` sets its own `closed` flag BEFORE the socket's close event
    // fires, so `onState("closed")` never reaches us — which means the branch
    // that normally restarts the local loop does not run. Without this,
    // pressing "Desconectar" in the headset silently ends head-yaw steering:
    // the stream is gone and the loop that would take over was stopped when
    // the stream opened. It only came back if the operator happened to press
    // a walk button.
    ensureLoopRunning();
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
        // Stale is dimmed in the headset rather than hidden: losing the view
        // mid-motion is worse than an obviously old one.
        vr?.setCameraLive(camLive);
      },
      onStreamUrl: (url) => {
        camFrameUrl = url;
        // Same URL to the headset. It changes on every reconnect (the source
        // cache-busts it), and the XR layer must follow — reusing a dead MJPEG
        // URL is a retry that does nothing, so a stall would otherwise freeze
        // the headset view on its last frame for the rest of the session.
        vr?.setCameraStream(url);
      },
    });
  }

  // An $effect rather than a one-shot in onMount, because `realHardware` can
  // become true LATER and nothing used to notice.
  //
  // It is derived from `data.env`, which comes from /state — apps/back, then
  // the bridge over port 8001. Open this page while any of that is still
  // coming up (very easy during a rushed setup) and `data.env` is null, so
  // `connectCamera()` never ran, the camera panel and its Reconectar button
  // were not rendered at all, and the headset got an empty layer. Nothing
  // re-ran it when /state started working: `refreshRole`'s invalidateAll
  // updates `data.env` without touching the camera. The only recovery was
  // reloading the page — in a headset, with no keyboard.
  $effect(() => {
    if (!realHardware) return;
    if (camHandle) return;
    connectCamera();
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
    // The readout timer is separate from the control loop and `stopLoop()`
    // does not touch it. Left running, it fires every 200 ms and writes state
    // on a destroyed component for as long as the tab is open, and every
    // return to this page adds another one.
    stopReadouts();
    camHandle?.close();
    camHandle = null;
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
  // Gestures need the robot in a locomotion FSM — every one of them declares
  // `fsm_state_in_{walk,walk_waist,run}`. Nothing in apps/back enforces it and
  // the bridge deliberately does not pre-check, so the FIRMWARE refuses with
  // error 7302, which arrives as a 502 and renders as a bare "Falló". That is
  // the "go debug the robot" signal this page exists to avoid: the robot is
  // fine, it is simply standing still.
  const GESTURE_FSM = ["walk", "walk_waist", "run"];
  const canGesture = $derived(GESTURE_FSM.includes(live.state?.posture ?? ""));

  //: An outcome now carries its own words, because the interesting failures are
  //: all distinguishable and used to be indistinguishable.
  //:
  //: THE BRIDGE NEVER RAISES. `run_g1_request` RETURNS `{status: "failed",
  //: phase: "rpc_error", error: "rpc_error_code_7401"}` on a firmware refusal,
  //: so FastMCP reports no error, apps/back returns HTTP 200, and this page —
  //: which read only the HTTP status — called it success. There is no `ok`
  //: branch on a gesture tile, so a refused gesture rendered NOTHING AT ALL:
  //: the tile dimmed for a few seconds, un-dimmed, and said nothing whether it
  //: waved or was refused. And a bring-up step that provably did nothing
  //: rendered "Enviado" in cyan.
  //:
  //: The diagnosis was in the response body the whole time. The bridge already
  //: writes `phase`, `error` and a `note` explaining exactly what to do.
  type PresetOutcome = { kind: "ok" | "warn" | "error"; text: string } | null;

  //: Firmware codes worth naming. Everything else falls through to the code
  //: itself, which is still better than "Falló".
  const RPC_HINTS: Record<string, string> = {
    // The arm is holding a sustained pose from a previous gesture. There is a
    // button for this in the same grid, and nothing used to point at it.
    rpc_error_code_7401:
      "Un brazo quedó sosteniendo la pose anterior — tocá «Relajar brazos»",
    // Something else owns rt/arm_sdk: the arm mirror, or a colleague's stack.
    rpc_error_code_7400:
      "rt/arm_sdk está ocupado — apagá el espejo de brazos (o el stack del compañero)",
    rpc_error_code_7404:
      "El servicio de brazos rechazó el gesto en este estado",
  };
  let presetBusy = $state<string | null>(null);
  let presetOutcome = $state<Record<string, PresetOutcome>>({});

  function httpOutcome(status: number): PresetOutcome {
    if (status === 403) return { kind: "warn", text: "Requiere admin" };
    // 502 is the ONLY thing apps/back turns a bridge problem into, and it means
    // the bridge process itself is unreachable — not that the robot refused.
    // A firmware refusal is a 200; see the note on PresetOutcome.
    if (status === 502)
      return {
        kind: "error",
        text: "El puente no responde — ¿está corriendo run_c3po?",
      };
    return { kind: "error", text: `Falló (HTTP ${status})` };
  }

  function bodyOutcome(body: Record<string, unknown> | null): PresetOutcome {
    if (!body) return { kind: "ok", text: "Enviado" };

    const phase = typeof body.phase === "string" ? body.phase : "";
    const err = typeof body.error === "string" ? body.error : "";
    const result = (body.result ?? {}) as Record<string, unknown>;

    if (body.status === "failed" || err) {
      return { kind: "error", text: RPC_HINTS[err] ?? (err || "Falló") };
    }

    // The firmware acks FSM ids it will not honour — SetFsmId(99999) returns 0
    // — so a zero code proves nothing about a bring-up step. The bridge checks
    // afterwards and says so; this used to print "Enviado" over the top of it.
    if (phase === "acked_no_transition") {
      const note = typeof body.note === "string" ? body.note : "";
      return {
        kind: "error",
        text: note || "Aceptado, pero el robot NO cambió de estado",
      };
    }
    if (result.transitioned === false) {
      return {
        kind: "error",
        text: "Aceptado, pero el robot NO cambió de estado",
      };
    }
    if (result.transitioned === true) {
      return { kind: "ok", text: "Listo" };
    }
    // `transitioned: null` means unverified, which is not the same as failed
    // and must not be reported as either.
    if (result.transitioned === null) {
      return { kind: "warn", text: "Enviado (sin confirmar)" };
    }
    return { kind: "ok", text: "Enviado" };
  }

  async function runPreset(name: string) {
    if (presetBusy) return;
    presetBusy = name;
    presetOutcome = { ...presetOutcome, [name]: null };
    try {
      const { data, error } = await createApi(fetch)
        .skills({ name })
        .invoke.post({});
      const status = error ? (error.status as number) : 0;
      if (status === 401) {
        if (browser) await goto("/login");
        return;
      }
      presetOutcome = {
        ...presetOutcome,
        [name]: error
          ? httpOutcome(status)
          : bodyOutcome(data as Record<string, unknown> | null),
      };
    } catch (err) {
      presetOutcome = {
        ...presetOutcome,
        [name]: {
          kind: "error",
          text: err instanceof Error ? err.message : "No se pudo enviar",
        },
      };
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
      class="flex flex-col items-start gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 text-sm text-ink"
    >
      <div class="flex items-start gap-2">
        <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger-soft" />
        <p>
          Esta cuenta no figura como admin. <strong
            >Los gestos y la puesta en marcha van a fallar con 403</strong
          >
          — invocar skills directamente requiere rol admin. Caminar y girar con la
          cabeza SÍ funcionan: van por el stream de teleoperación, que no pasa por
          esa puerta. PARAR también funciona siempre, porque está clasificado como
          skill de seguridad.
          <br />
          <span class="text-ink-soft"
            >Si el rol se cambió recién, puede ser sólo la sesión guardada: se
            cachea hasta 5 minutos. Refrescala acá antes de tocar nada más.</span
          >
        </p>
      </div>
      <button
        type="button"
        class="rounded-md border border-danger/40 px-2.5 py-1.5 text-xs font-medium text-ink hover:bg-danger/10 disabled:opacity-50"
        disabled={refreshingRole}
        onclick={refreshRole}
      >
        {refreshingRole ? "Verificando…" : "Volver a verificar el rol"}
      </button>
      {#if roleRefreshOutcome}
        <p class="text-ink-soft text-xs">{roleRefreshOutcome}</p>
      {/if}
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
          {#if presetBusy === step.name}
            <span class="text-xs text-ink-mute">Enviando…</span>
          {:else if presetOutcome[step.name]}
            <span
              class="text-xs {presetOutcome[step.name]!.kind === 'ok'
                ? 'text-cyan'
                : presetOutcome[step.name]!.kind === 'warn'
                  ? 'text-warn'
                  : 'text-danger-soft'}">{presetOutcome[step.name]!.text}</span
            >
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
    {#if linkLost}
      <p
        role="alert"
        class="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>Se perdió el enlace USB.</strong> La página ya no alcanza su
        propio servidor, así que los reenvíos de <code>adb reverse</code> se
        cayeron — normalmente porque se desconectó el cable del visor. En la
        Mac:
        <code>./scripts/quest_setup.sh</code>. El robot está bien; lo que se
        cortó es el camino hasta él.
      </p>
    {/if}

    {#if vrActive && xrCameraBroken}
      <p
        role="alert"
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>La capa de cámara falló al dibujar.</strong> Es un problema de WebGL
        en el visor — un shader que no compiló —, no del robot ni de la cámara. El
        seguimiento de cabeza sigue funcionando: se descartó la imagen y se conservó
        la pose, a propósito. Salí y volvé a entrar a VR para reintentar con un contexto
        nuevo.
      </p>
    {:else if vrActive && !realHardware}
      <p
        role="alert"
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>La cámara ni siquiera se intentó.</strong> El puente no reportó
        <code>SIM_MODE=real</code>, así que esta página nunca abrió el stream —
        no es un problema de la cámara ni del túnel. Suele ser que falta
        <code>-L 8001</code> en el túnel, que <code>run_c3po</code> no está
        corriendo, o que <code>apps/bridge/.env</code> sigue en
        <code>stub</code>. Corré <code>./scripts/preflight.sh</code>.
      </p>
    {:else if vrActive && !xrCameraEverHadFrame}
      <p
        role="alert"
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>Sin imagen en el visor.</strong> No llegó ningún cuadro de la
        cámara. Casi siempre es que <code>perception_up perception</code> no está
        corriendo en el robot — es el proceso que tiene la D435i y sirve el stream
        en 8081.
      </p>
    {:else if vrActive && !xrCameraLive}
      <p
        role="alert"
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>La imagen quedó congelada.</strong> Llegaron cuadros y dejaron
        de llegar, así que <code>perception_up</code> SÍ está corriendo — lo que falló
        son sus ticks, o el enlace. Lo que ves en el visor es una foto vieja, atenuada
        a propósito para que se note. No manejes con eso.
      </p>
    {/if}

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
        {:else if teleopState === "stalled"}
          <span class="text-warn"
            >Sin respuesta del puente — {teleopDetail || "el enlace se colgó"}.
            El robot ya se detuvo solo. Reconectá.</span
          >
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
        <strong>Detenido por PARAR.</strong> El puente cortó el movimiento.
        Soltá los controles y la cabeza al centro, y mantenelos así
        <strong>un segundo completo</strong> para volver a habilitarlo. Soltar y volver
        a apretar en el acto no alcanza: es justo lo que uno hace por reflejo al frenar
        de golpe, y no puede ser lo que reanuda al robot. Reconectar tampoco lo borra
        — la parada sigue en pie hasta que alguien la libera acá.
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
    {#if data.catalogueFailed}
      <p
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>No se pudo leer el catálogo de skills.</strong> Los badges de
        "Sin probar en real" no se muestran — <em>no</em> quiere decir que todo esté
        verificado, quiere decir que no sabemos. Revisá que el puente esté corriendo
        y el túnel abierto.
      </p>
    {/if}

    {#if !canGesture}
      <p
        class="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm text-ink"
      >
        <strong>Los gestos necesitan al robot en marcha.</strong> El firmware
        los rechaza (error 7302) desde cualquier otro estado — ahora está en
        <strong>{posture.label}</strong>. Hacé <em>Puesta en marcha</em> arriba (pasos
        1 → 2 → 3) y después probá de nuevo. No es una falla del robot.
      </p>
    {/if}
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
          {#if presetBusy === preset.name}
            <span class="text-xs text-ink-mute">Enviando…</span>
          {:else if presetOutcome[preset.name]}
            <span
              class="text-xs {presetOutcome[preset.name]!.kind === 'ok'
                ? 'text-cyan'
                : presetOutcome[preset.name]!.kind === 'warn'
                  ? 'text-warn'
                  : 'text-danger-soft'}"
              >{presetOutcome[preset.name]!.text}</span
            >
          {/if}
        </button>
      {/each}
    </div>
  </section>
</div>
