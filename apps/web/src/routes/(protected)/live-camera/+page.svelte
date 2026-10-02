<script lang="ts">
  import { onMount } from "svelte";
  import { env } from "$env/dynamic/public";
  import {
    Camera,
    ExternalLink,
    Maximize2,
    Minimize2,
    RotateCw,
  } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import RobotCameraView from "$lib/components/robot-camera-view.svelte";
  import CamOverlay from "$lib/components/robot/cam-overlay.svelte";
  import {
    connectSimCamera,
    type SimCamHandle,
    type SimCamState,
  } from "$lib/webrtc/sim-camera";
  import {
    connectRobotCamera,
    endpoint,
    type RobotCamHandle,
    type RobotCamState,
    type RobotCamStatus,
  } from "$lib/robot/mjpeg-camera";
  import {
    CAM_BADGE_LABEL,
    CAM_BADGE_TONE,
    NO_FRAME,
    SOURCE_LABEL,
    SOURCE_TECH,
    describeSimCam,
    robotCamBadge,
    snapshotFailure,
    cameraHint,
    type SnapshotFailure,
  } from "$lib/robot/camera-copy";
  import { formatAge } from "$lib/robot/format";

  // TWO SOURCES, AND THEY ARE NOT INTERCHANGEABLE.
  //
  // The simulator serves three WebRTC cameras (teleimager/aiortc, ports
  // 60001-3). The real G1 has one head camera, served as MJPEG by the bridge's
  // :8001/camera relay from whichever of two servers currently owns the device
  // (the vendor's videohub feed, or apps/perception's vision container). This
  // page used to know only the first case, which made it a permanently dead
  // panel on hardware.
  //
  // PUBLIC_ROBOT_CAM_URL is what picks: set it and the console shows the
  // robot. The robot wins over the sim when both are configured — an operator
  // looking at a console attached to a physical robot must not be shown a
  // simulation. Read at the top level, not in onMount: dynamic public env is
  // available during SSR, and reading it late rendered the sim layout
  // (":60001", two wrist tiles) on the robot console until hydration.
  const robotBase = (env.PUBLIC_ROBOT_CAM_URL ?? "").trim();
  const robotMode = robotBase !== "";

  // The sim's teleimager image servers — one aiortc WebRTC server per camera.
  // Head is binocular (480x1280 side-by-side); the main panel shows exactly
  // the left eye (see the head-feed wrapper below). Wrists are monocular.
  const cameras = [
    { id: "head", label: "Cabeza", port: 60001 },
    { id: "lwrist", label: "Muñeca izq.", port: 60002 },
    { id: "rwrist", label: "Muñeca der.", port: 60003 },
  ] as const;
  type CamId = (typeof cameras)[number]["id"];
  const wrists = cameras.filter((c) => c.id !== "head");

  type Runtime = {
    stream: MediaStream | null;
    state: SimCamState;
    detail: string;
  };
  const blank = (): Runtime => ({
    stream: null,
    state: "connecting",
    detail: "",
  });
  let rt = $state<Record<CamId, Runtime>>({
    head: blank(),
    lwrist: blank(),
    rwrist: blank(),
  });
  const handles: Partial<Record<CamId, SimCamHandle>> = {};

  // Default to the host serving the console (browser runs on the sim box), so no
  // config is needed in the co-located setup; override with PUBLIC_SIM_CAM_HOST.
  // `location` only exists in the browser, so the fallback lands in onMount.
  let host = $state((env.PUBLIC_SIM_CAM_HOST ?? "").trim());
  const videoEls: Partial<Record<CamId, HTMLVideoElement>> = {};
  // Fullscreen takes the whole canvas, never the bare <img>/<video>: the
  // state badge and the overlays have to go with the picture — a full-screen
  // frozen frame with nothing saying so is exactly what this page refuses
  // to show.
  let canvasEl: HTMLDivElement | null = null;
  let fullscreen = $state(false);
  const canvasRef = (node: HTMLDivElement) => {
    canvasEl = node;
    return () => {
      if (canvasEl === node) canvasEl = null;
    };
  };

  function start(id: CamId, port: number) {
    handles[id]?.close();
    rt[id] = blank();
    // Statement bodies, not expression bodies: returning the assignment made
    // Svelte warn (`assignment_value_stale`) because the value handed back is
    // the right-hand side rather than the proxied state it just wrote.
    handles[id] = connectSimCamera(host, port, {
      onStream: (stream) => {
        rt[id] = { ...rt[id], stream };
      },
      onState: (state, detail) => {
        rt[id] = { ...rt[id], state, detail: detail ?? "" };
      },
    });
  }

  // --- the real robot: one camera, MJPEG, truth from /status ---------------

  let robot = $state<{
    state: RobotCamState;
    detail: string | null;
    status: RobotCamStatus | null;
    url: string | null;
    fps: number | null;
  }>({
    state: "connecting",
    detail: null,
    status: null,
    url: null,
    fps: null,
  });
  let robotHandle: RobotCamHandle | null = null;
  // The stream URL the <img> failed on, reported by RobotCameraView. Only the
  // view can see an image error; without it this page's badge, side panel
  // and snapshot button said "en vivo" over a picture the view itself was
  // labelling SIN SEÑAL.
  let failedUrl = $state<string | null>(null);
  // Frame rate is measured across /status polls rather than trusted from a
  // config value: a Jetson under load delivers what it delivers, and that is
  // the number worth showing.
  let lastFrames = 0;
  let lastFramesAt = 0;

  function startRobot() {
    robotHandle?.close();
    lastFrames = 0;
    lastFramesAt = 0;
    robot = { ...robot, status: null, fps: null };
    robotHandle = connectRobotCamera(robotBase, {
      onState: (state, detail) => {
        robot = { ...robot, state, detail: detail ?? null };
      },
      onStatus: (status) => {
        const now = Date.now();
        let fps = robot.fps;
        if (status && lastFramesAt > 0 && now > lastFramesAt) {
          const delta = status.frames - lastFrames;
          fps = delta <= 0 ? 0 : (delta * 1000) / (now - lastFramesAt);
        }
        if (status) {
          lastFrames = status.frames;
          lastFramesAt = now;
        }
        robot = { ...robot, status, fps: status ? fps : null };
      },
      onStreamUrl: (url) => {
        robot = { ...robot, url };
      },
    });
  }

  function stopRobot() {
    robotHandle?.close();
    robotHandle = null;
    robot = { ...robot, url: null, fps: null };
  }

  function reconnectAll() {
    if (robotMode) {
      startRobot();
      return;
    }
    for (const c of cameras) start(c.id, c.port);
  }

  onMount(() => {
    if (!host) host = location.hostname;
    reconnectAll();
    return () => {
      robotHandle?.close();
      for (const c of cameras) handles[c.id]?.close();
    };
  });

  // A hidden tab has nobody watching: stop the 2 Hz status poll and drop the
  // <img> (an MJPEG stream keeps flowing — up to 1920x1080 — over the robot's
  // Wi-Fi even when timers are throttled), and reopen on return. The sim's
  // WebRTC feeds are left alone: they are local to the sim box, and
  // reconnecting them costs a certificate round-trip per port.
  function onvisibilitychange() {
    if (!robotMode) return;
    if (document.visibilityState === "hidden") stopRobot();
    else if (!robotHandle) startRobot();
  }

  function onfullscreenchange() {
    fullscreen = document.fullscreenElement === canvasEl;
  }

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await canvasEl?.requestFullscreen();
    } catch {
      toast.error("El navegador no permitió la pantalla completa.");
    }
  }

  // <video>.srcObject can only be assigned imperatively; the attachment also
  // keeps a ref to each <video> so the snapshot can read the head feed.
  function video(id: CamId, stream: MediaStream | null) {
    return (node: HTMLVideoElement) => {
      // The attachment re-runs whenever the feed's runtime object is replaced
      // (every state change). Assigning srcObject restarts the media element
      // even for the same stream, so only assign when it actually changed.
      if (node.srcObject !== stream) node.srcObject = stream;
      videoEls[id] = node;
      return () => {
        if (videoEls[id] === node) delete videoEls[id];
      };
    };
  }

  // --- snapshot -----------------------------------------------------------

  const stamp = () =>
    new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);

  function download(blob: Blob, name: string) {
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }

  let capturing = $state(false);
  // The last snapshot failure, for the side panel: a toast cannot carry the
  // bridge's verbatim (English) words as a lang="en" readout, so they land
  // here. Cleared by the next successful capture.
  let snapshotError = $state<SnapshotFailure | null>(null);

  async function snapshot() {
    if (capturing) return;
    capturing = true;
    try {
      if (robotMode) {
        // The bridge's newest frame as one JPEG — a real frame from the
        // source, not a screenshot of a possibly-stale <img>. A 503 carries
        // the reason ("no frame yet") and the hint.
        const res = await fetch(endpoint(robotBase, "frame.jpg"), {
          cache: "no-store",
          signal: AbortSignal.timeout(5000),
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => null)) as {
            error?: string;
          } | null;
          // "HTTP 503: no frame yet" — the shape snapshotFailure() parses.
          throw new Error(
            body?.error
              ? `HTTP ${res.status}: ${body.error}`
              : `HTTP ${res.status}`,
          );
        }
        download(await res.blob(), `c3po-camara-${stamp()}.jpg`);
      } else {
        // The head stream is side-by-side stereo; the capture is the LEFT
        // eye only, matching what the panel shows.
        const v = videoEls.head;
        if (!v || !v.videoWidth) throw new Error(NO_FRAME);
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(v.videoWidth / 2);
        canvas.height = v.videoHeight;
        canvas
          .getContext("2d")
          ?.drawImage(
            v,
            0,
            0,
            canvas.width,
            canvas.height,
            0,
            0,
            canvas.width,
            canvas.height,
          );
        const blob = await new Promise<Blob | null>((r) =>
          canvas.toBlob(r, "image/png"),
        );
        if (!blob) throw new Error(NO_FRAME);
        download(blob, `c3po-sim-cabeza-${stamp()}.png`);
      }
      snapshotError = null;
      toast.success("Captura guardada.");
    } catch (err) {
      // Spanish in the toast; the raw words (often the bridge's English)
      // only in the side panel's readout, marked lang="en" (camera-copy.ts).
      const failure = snapshotFailure(err);
      snapshotError = failure;
      toast.error("No se pudo capturar la imagen.", {
        description: failure.es,
      });
    } finally {
      capturing = false;
    }
  }

  // --- what the badge and the toolbar say ---------------------------------

  const headCopy = $derived(
    describeSimCam(
      rt.head.state,
      rt.head.detail || null,
      `${host || "este host"}:60001`,
    ),
  );
  const liveCount = $derived(
    cameras.filter((c) => rt[c.id].state === "live").length,
  );
  const imgBroken = $derived(robot.url !== null && failedUrl === robot.url);
  const robotBadge = $derived(
    robotCamBadge(robot.state, robot.status, imgBroken),
  );
  const mainBadge = $derived(robotMode ? robotBadge : headCopy.badge);
  const hasPicture = $derived(
    robotMode ? robot.url !== null && !imgBroken : rt.head.stream !== null,
  );
  const source = $derived(
    robot.status?.source && robot.status.source !== "none"
      ? robot.status.source
      : null,
  );

  // One chip treatment for text laid over the picture.
  const chip =
    "rounded-md border border-hairline bg-abyss/80 px-2 py-1 backdrop-blur-sm";
</script>

<svelte:document {onvisibilitychange} {onfullscreenchange} />

{#snippet retry(onclick: () => void)}
  <button
    type="button"
    {onclick}
    class="inline-flex min-h-9 items-center gap-1.5 tile-interactive px-3 py-1.5 text-sm text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
  >
    <RotateCw class="size-3.5" aria-hidden="true" /> Reintentar
  </button>
{/snippet}

{#snippet simFeed(id: CamId, port: number, mainFeed: boolean)}
  {@const r = rt[id]}
  {@const copy = describeSimCam(
    r.state,
    r.detail || null,
    `${host || "este host"}:${port}`,
  )}
  {#if r.stream}
    {#if mainFeed}
      <!-- The head stream is 1280x480 side-by-side stereo. A 4:3 window
           (one eye), with the video at 200 % width inside it, shows EXACTLY
           the left eye at any panel aspect: object-cover used to crop part of
           it on a square canvas and leak a strip of the right eye on a wide
           one. Letterboxed, never cropped. -->
      <div
        class="@container-size absolute inset-0 z-10 flex items-center justify-center"
      >
        <div
          class="relative aspect-[4/3] overflow-hidden"
          style="width: min(100cqw, calc(100cqh * 4 / 3))"
        >
          <video
            {@attach video(id, r.stream)}
            autoplay
            playsinline
            muted
            aria-label="Cámara de la cabeza, ojo izquierdo (simulador)"
            class="absolute top-0 left-0 h-full w-[200%] max-w-none object-contain"
          ></video>
        </div>
      </div>
    {:else}
      <video
        {@attach video(id, r.stream)}
        autoplay
        playsinline
        muted
        aria-label="Cámara de la {cameras
          .find((c) => c.id === id)
          ?.label.toLowerCase()} (simulador)"
        class="absolute inset-0 z-10 size-full object-contain"
      ></video>
    {/if}
  {/if}
  {#if copy.overlay}
    <CamOverlay overlay={copy.overlay} compact={!mainFeed}>
      {#snippet actions()}
        {#if copy.overlay?.kind === "message"}
          {#if copy.certLinks}
            <!-- All three ports on the main panel: accepting only :60001 gets
                 the head feed back and leaves both wrists dead with no
                 explanation, which reads as a second, unrelated failure. -->
            {#each mainFeed ? cameras : [{ id, port, label: "" }] as cam (cam.port)}
              <a
                href="https://{host}:{cam.port}"
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex min-h-9 items-center gap-1.5 tile-interactive px-3 py-1.5 text-sm text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
              >
                :{cam.port}
                <ExternalLink class="size-3.5" aria-hidden="true" />
              </a>
            {/each}
          {/if}
          {@render retry(() => (mainFeed ? reconnectAll() : start(id, port)))}
        {/if}
      {/snippet}
    </CamOverlay>
  {/if}
{/snippet}

<div class="flex h-full min-h-0 flex-col gap-4 pb-2 lg:flex-row">
  <!-- Taller on phones. The overlay that explains a dead feed (a title, a
       sentence, the bridge's own words, Reconectar) did not fit a ~330 px
       canvas: it scrolled under the state badge and the button sat on the
       footer readouts. A live picture letterboxes into the extra height. -->
  <section
    class="flex min-h-[460px] min-w-0 flex-1 flex-col overflow-hidden panel sm:min-h-[380px]"
    aria-label="Cámara principal"
  >
    <!-- One row at every width. There used to be a "Reconectar" here as
         well as the one in the picture's overlay: two buttons for one action,
         and on a phone this one wrapped the toolbar and left fullscreen alone
         on a second row. The overlay's stays — it appears exactly when the
         feed is not live, next to the sentence that says why. -->
    <div
      class="flex items-center gap-2 border-b border-hairline bg-abyss px-4 py-3"
    >
      <span class="min-w-0 flex-1 truncate readout">
        {robotMode ? "G1 · cámara frontal" : "SIM · teleimager"}
      </span>
      <button
        type="button"
        onclick={snapshot}
        disabled={mainBadge !== "live" || capturing}
        aria-label="Captura"
        title="Captura"
        class="inline-flex size-9 items-center justify-center rounded-full border border-hairline bg-wash text-ink transition-colors hover:border-accent-edge hover:bg-wash-hover focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40 sm:size-8"
      >
        <Camera class="size-3.5" aria-hidden="true" />
      </button>
      <button
        type="button"
        onclick={toggleFullscreen}
        disabled={!hasPicture && !fullscreen}
        aria-label={fullscreen
          ? "Salir de pantalla completa"
          : "Pantalla completa"}
        title={fullscreen ? "Salir de pantalla completa" : "Pantalla completa"}
        class="inline-flex size-9 items-center justify-center rounded-full border border-hairline bg-wash text-ink transition-colors hover:border-accent-edge hover:bg-wash-hover focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40 sm:size-8"
      >
        {#if fullscreen}
          <Minimize2 class="size-3.5" aria-hidden="true" />
        {:else}
          <Maximize2 class="size-3.5" aria-hidden="true" />
        {/if}
      </button>
    </div>

    <!-- Feed canvas. Deliberately bare: this used to draw a vanishing-point
         "floor" and a HUD crosshair over it. Neither corresponded to anything —
         the crosshair aimed at nothing and the floor was a picture of a room
         that isn't there — so a dead camera looked like a working one. -->
    <div
      {@attach canvasRef}
      class="relative min-h-0 flex-1 overflow-hidden bg-trench"
    >
      {#if robotMode}
        <RobotCameraView
          class="absolute inset-0"
          state={robot.state}
          detail={robot.detail}
          url={robot.url}
          status={robot.status}
          bind:failedUrl
          onreconnect={() =>
            robotHandle ? robotHandle.reconnect() : startRobot()}
        />
        <!-- Chips with a fill: the overlay scrolls underneath them on a
             small canvas, and bare text over a message was unreadable. -->
        <div
          class="pointer-events-none absolute inset-x-3 bottom-3 z-30 flex flex-wrap items-end justify-between gap-2 readout"
        >
          <!-- Measured, not configured: a loaded Jetson delivers what it
               delivers. -->
          <span class={chip}>
            {robot.status?.stream_width && robot.status?.stream_height
              ? `${robot.status.stream_width}×${robot.status.stream_height}`
              : "—"} · {robot.fps === null ? "—" : robot.fps.toFixed(1)} fps
          </span>
          <span class={chip}
            >MJPEG · {source ? SOURCE_LABEL[source] : "sin fuente"}</span
          >
        </div>
      {:else}
        {@render simFeed("head", 60001, true)}
        <div class="absolute top-3 left-3 z-30 flex flex-col items-start gap-1">
          <span
            role="status"
            class="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-2xs tracking-[0.1em] uppercase {CAM_BADGE_TONE[
              headCopy.badge
            ].text}"
          >
            <span
              class="size-1.5 rounded-full {CAM_BADGE_TONE[headCopy.badge].dot}"
              aria-hidden="true"
            ></span>
            {CAM_BADGE_LABEL[headCopy.badge]}
          </span>
        </div>
        <!-- Only the state badge in the top corner, as in robot mode; the
             rest moved down here, where it no longer sat on top of the
             overlay's message. -->
        <div
          class="pointer-events-none absolute inset-x-3 bottom-3 z-30 flex flex-wrap items-end justify-between gap-2 readout"
        >
          <span class={chip}
            ><span class="text-ink">Cabeza · ojo izq.</span> · {host ||
              "…"}:60001</span
          >
          <span class={chip}>{liveCount}/3 en vivo · WebRTC</span>
        </div>
      {/if}
    </div>
  </section>

  <aside
    class="flex shrink-0 flex-col gap-4 panel p-4 lg:w-[360px] lg:overflow-y-auto"
  >
    <!-- No count beside the heading: a bare "1" next to "Esta cámara" read
         as a value nobody explained. -->
    <h2 class="eyebrow">{robotMode ? "Cámara frontal" : "Otras cámaras"}</h2>
    {#if robotMode}
      <!-- The G1 has ONE head camera and the console should say so plainly.
           Earlier versions showed head + two wrist tiles because the SIM has
           three; on this robot the wrist and chest cameras are not "offline",
           they are not fitted (docs/ROBOT-HARDWARE.md §6).

           Every absent value is the same mono "—" in the muted ink: they
           used to render in two fonts, which made one em dash look like an
           en dash beside the others. -->
      {#snippet value(v: string | number | null | undefined, mono = true)}
        {#if v == null || v === ""}
          <span class="font-mono text-ink-mute">—</span>
        {:else}
          <span class={[mono && "font-mono", "text-ink"]}>{v}</span>
        {/if}
      {/snippet}
      <dl class="flex flex-col gap-2 text-sm">
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Estado</dt>
          <!-- From the same badge as the picture, image errors included. -->
          <dd>
            {@render value(CAM_BADGE_LABEL[robotBadge].toLowerCase(), false)}
          </dd>
        </div>
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Fuente</dt>
          <!-- "ninguna" is the bridge SAYING no feed is live; "—" is not
               knowing (no /status). -->
          <dd class="flex flex-col items-end text-right">
            {@render value(
              robot.status?.source ? SOURCE_LABEL[robot.status.source] : null,
              false,
            )}
            {#if source && SOURCE_TECH[source]}
              <span class="readout">{SOURCE_TECH[source]}</span>
            {/if}
          </dd>
        </div>
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Último cuadro</dt>
          <dd>
            {@render value(
              robot.status?.frame_age_s == null
                ? null
                : robot.status.frame_age_s < 10
                  ? `hace ${robot.status.frame_age_s.toFixed(2)} s`
                  : formatAge(robot.status.frame_age_s),
            )}
          </dd>
        </div>
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Cuadros servidos</dt>
          <dd>{@render value(robot.status?.frames)}</dd>
        </div>
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Sensor</dt>
          <dd>
            {@render value(
              robot.status?.width && robot.status?.height
                ? `${robot.status.width}×${robot.status.height}`
                : null,
            )}
          </dd>
        </div>
      </dl>

      {#if snapshotError}
        <!-- The last failed capture. The toast said it in Spanish; the raw
             words (often the bridge's English) are kept here, where they can
             be marked as such and copied. -->
        <div class="flex flex-col gap-0.5 border-t border-hairline pt-3">
          <h3 class="eyebrow">Última captura</h3>
          <p class="text-sm text-danger-soft">{snapshotError.es}</p>
          {#if snapshotError.raw}
            <p class="readout break-words" lang="en">{snapshotError.raw}</p>
          {/if}
        </div>
      {/if}

      {#if robot.status?.sources}
        <!-- Both feeds, always: an operator debugging a dark picture needs
             to know that the OTHER one was also asked, and what it said. -->
        <div class="flex flex-col gap-2 border-t border-hairline pt-3">
          <h3 class="eyebrow">Fuentes</h3>
          <ul class="flex flex-col gap-2.5 text-sm">
            {#each [["videohub", robot.status.sources.videohub], ["vision", robot.status.sources.vision]] as const as [key, s] (key)}
              {@const hint = s?.live ? null : cameraHint(s?.hint)}
              <li class="flex flex-col gap-0.5">
                <span class="flex items-baseline justify-between gap-3">
                  <span class="inline-block text-ink first-letter:uppercase"
                    >{SOURCE_LABEL[key]}</span
                  >
                  <span class={s?.live ? "text-ok" : "text-ink-mute"}>
                    {s?.live ? "en vivo" : "sin cuadros"}
                  </span>
                </span>
                <span class="readout">{SOURCE_TECH[key]}</span>
                {#if hint?.es}
                  <span class="text-xs leading-relaxed text-ink-dim"
                    >{hint.es}</span
                  >
                {/if}
                {#if hint}
                  <span class="readout break-words" lang="en">{hint.en}</span>
                {/if}
              </li>
            {/each}
          </ul>
        </div>
      {/if}

      <p class="text-xs leading-relaxed text-ink-mute">
        El G1 tiene una sola cámara frontal, y la pueden servir dos fuentes,
        nunca las dos a la vez: la del fabricante (más resolución) o la de
        visión, que además alimenta el detector de objetos. Esta consola muestra
        la que esté entregando imagen.
      </p>
      <!-- The address on a line of its own: break-all is right for a URL
           inside a readout and wrong in prose, where it split "http" into
           "htt / p". -->
      <div class="flex flex-col gap-0.5">
        <span class="eyebrow">Dirección del puente</span>
        <span class="readout break-all text-ink-dim">{robotBase}</span>
      </div>
    {:else}
      <!-- Single column on phones: side by side, the camera label and the
         "sin señal" overlay collide in a ~180px-wide tile. -->
      <div
        class="grid min-h-0 flex-1 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-1"
      >
        {#each wrists as cam (cam.id)}
          {@const tileCopy = describeSimCam(
            rt[cam.id].state,
            rt[cam.id].detail || null,
            "",
          )}
          <div
            class="relative aspect-video min-h-0 overflow-hidden rounded-lg border border-hairline bg-trench lg:aspect-auto lg:flex-1"
          >
            {@render simFeed(cam.id, cam.port, false)}
            <span
              class="absolute top-2 left-2.5 z-30 flex items-center gap-1.5 font-mono text-2xs tracking-wide uppercase {rt[
                cam.id
              ].state === 'live'
                ? 'text-ok'
                : 'text-ink-mute'}"
            >
              <span
                class="size-1.5 rounded-full {CAM_BADGE_TONE[tileCopy.badge]
                  .dot}"
                aria-hidden="true"
              ></span>
              {cam.label}
              <span class="sr-only">: {tileCopy.short}</span>
            </span>
          </div>
        {/each}
      </div>
      <p class="readout leading-relaxed">
        Servidores teleimager en {host || "este host"}:60001–60003. El navegador
        debe estar en la red del simulador y aceptar el certificado de cada
        puerto.
      </p>
    {/if}
  </aside>
</div>
