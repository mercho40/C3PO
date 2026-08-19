<script lang="ts">
  import { onMount } from "svelte";
  import { env } from "$env/dynamic/public";
  import { Maximize2, RotateCw, VideoOff, ExternalLink } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import {
    connectSimCamera,
    type SimCamHandle,
    type SimCamState,
  } from "$lib/webrtc/sim-camera";
  import {
    connectRobotCamera,
    type RobotCamHandle,
    type RobotCamState,
    type RobotCamStatus,
  } from "$lib/robot/mjpeg-camera";

  // TWO SOURCES, AND THEY ARE NOT INTERCHANGEABLE.
  //
  // The simulator serves three WebRTC cameras (teleimager/aiortc, ports
  // 60001-3). The real G1 serves none of that: its only camera is the D435i
  // colour node, and the picture comes from apps/perception's vision container
  // as MJPEG, because that container already holds /dev/video4 and a V4L2 node
  // has exactly one owner. This page used to know only the first case, which
  // made it a permanently dead panel on hardware.
  //
  // PUBLIC_ROBOT_CAM_URL is what picks: set it (normally to a port forwarded
  // over the same SSH tunnel as the bridge) and the console shows the robot.
  // The robot wins over the sim when both are configured — an operator looking
  // at a console attached to a physical robot must not be shown a simulation.
  let robotBase = $state("");
  const robotMode = $derived(robotBase !== "");

  // The sim's teleimager image servers — one aiortc WebRTC server per camera.
  // Head is binocular (480x1280 side-by-side); we bias the main feed to the
  // left eye via object-left. Wrists are monocular.
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
  let host = $state("");
  let videoEls = $state<Partial<Record<CamId, HTMLVideoElement>>>({});
  // The robot feed is an <img>, and fullscreening the image alone would drop the
  // state badge with it — a full-screen frozen picture with nothing saying so is
  // exactly the thing this page is careful not to show. Fullscreen the canvas.
  let canvasEl = $state<HTMLDivElement | null>(null);

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
    detail: string;
    status: RobotCamStatus | null;
    url: string;
    broken: boolean;
    fps: number | null;
  }>({
    state: "connecting",
    detail: "",
    status: null,
    url: "",
    broken: false,
    fps: null,
  });
  let robotHandle: RobotCamHandle | null = null;
  // Frame rate is measured across /status polls rather than trusted from a
  // config value: the container decimates to 5 Hz by default, but a Jetson
  // under load delivers what it delivers, and that is the number worth showing.
  let lastFrames = 0;
  let lastFramesAt = 0;

  function startRobot() {
    robotHandle?.close();
    lastFrames = 0;
    lastFramesAt = 0;
    robot = { ...robot, status: null, broken: false, fps: null };
    robotHandle = connectRobotCamera(robotBase, {
      onState: (state, detail) => {
        robot = { ...robot, state, detail: detail ?? "" };
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
        robot = { ...robot, url, broken: false };
      },
    });
  }

  function reconnectAll() {
    if (robotMode) {
      startRobot();
      return;
    }
    for (const c of cameras) start(c.id, c.port);
  }

  onMount(() => {
    robotBase = (env.PUBLIC_ROBOT_CAM_URL ?? "").trim();
    host = env.PUBLIC_SIM_CAM_HOST || location.hostname;
    reconnectAll();
    return () => {
      robotHandle?.close();
      for (const c of cameras) handles[c.id]?.close();
    };
  });

  // <video>.srcObject can only be assigned imperatively; the action also keeps a
  // ref to each <video> (keyed by camera) so the toolbar can fullscreen the head.
  function video(
    node: HTMLVideoElement,
    args: { id: CamId; stream: MediaStream | null },
  ) {
    node.srcObject = args.stream;
    videoEls[args.id] = node;
    return {
      update: (a: { id: CamId; stream: MediaStream | null }) =>
        (node.srcObject = a.stream),
      destroy: () => {
        if (videoEls[args.id] === node) delete videoEls[args.id];
      },
    };
  }

  const headState = $derived(rt.head.state);
  const liveCount = $derived(
    cameras.filter((c) => rt[c.id].state === "live").length,
  );

  // One badge vocabulary for both sources, so the operator reads the same three
  // words whichever robot they are attached to. "stale" has no sim equivalent —
  // WebRTC tears the track down instead of freezing on the last frame.
  const mainState = $derived<"live" | "connecting" | "stale" | "down">(
    robotMode
      ? robot.state === "live"
        ? "live"
        : robot.state === "connecting"
          ? "connecting"
          : robot.state === "stale"
            ? "stale"
            : "down"
      : headState === "live"
        ? "live"
        : headState === "connecting"
          ? "connecting"
          : "down",
  );
  const mainLabel = $derived(
    {
      live: "EN VIVO",
      connecting: "Conectando",
      stale: "Congelado",
      down: "Sin señal",
    }[mainState],
  );
</script>

{#snippet robotFeed()}
  <!-- object-CONTAIN, not cover: the D435i is 4:3 and this panel is not, and
       cropping a robot's field of view to make it fill a box hides obstacles
       the robot can actually see. Letterboxing is the honest framing. -->
  {#if robot.url && !robot.broken}
    <img
      src={robot.url}
      alt="Cámara frontal del robot"
      onerror={() => (robot = { ...robot, broken: true })}
      class="absolute inset-0 z-10 size-full object-contain"
    />
  {/if}
  {#if robot.state !== "live"}
    <div
      class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 px-5 text-center {robot.url &&
      !robot.broken &&
      robot.state === 'stale'
        ? 'bg-trench/55 backdrop-blur-[1px]'
        : 'bg-trench'}"
    >
      {#if robot.state === "connecting"}
        <div
          class="size-5 animate-spin rounded-full border-2 border-hairline-strong border-t-cyan"
        ></div>
        <span class="readout">Conectando…</span>
      {:else}
        <VideoOff class="size-7 text-ink-mute" />
        <div class="flex flex-col gap-2">
          <span class="stamp-quiet text-lg text-ink">
            {robot.state === "stale"
              ? "La imagen está congelada"
              : "No hay video del robot"}
          </span>
          <p class="max-w-[46ch] text-sm leading-relaxed text-ink-mute">
            {#if robot.state === "stale"}
              <!-- The distinction the whole /status endpoint exists for: the
                   picture on screen is real, it is just old. -->
              El contenedor de visión responde pero dejó de entregar cuadros ({robot.detail}).
              Lo que se ve es el último cuadro, no la escena actual.
            {:else if robot.detail === "tunnel"}
              No se puede alcanzar {robotBase}. Revisá el túnel SSH (<code
                class="font-mono text-2xs"
                >ssh -f -N -L 8081:127.0.0.1:8081 c3po</code
              >) y que <code class="font-mono text-2xs">perception_up</code> esté
              corriendo con la cámara.
            {:else}
              {robotBase} respondió con un error: {robot.detail}
            {/if}
          </p>
        </div>
        <button
          type="button"
          onclick={() => robotHandle?.reconnect()}
          class="inline-flex min-h-9 items-center gap-1.5 tile-interactive px-3 py-1.5 text-sm text-ink"
        >
          <RotateCw class="size-3.5" /> Reintentar
        </button>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet feed(id: CamId, port: number, mainFeed: boolean)}
  {@const r = rt[id]}
  {#if r.stream}
    <video
      use:video={{ id, stream: r.stream }}
      autoplay
      playsinline
      muted
      class="absolute inset-0 z-10 size-full {mainFeed
        ? 'object-cover object-left'
        : 'object-cover'}"
    ></video>
  {/if}
  {#if r.state !== "live"}
    <!-- An empty feed should say what happened and what to do about it. It used
         to render a fake perspective "room" with a HUD crosshair over it, which
         dressed a dead camera up as a working one. -->
    <div
      class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 px-5 text-center {r.stream
        ? 'bg-trench/55 backdrop-blur-[1px]'
        : ''}"
    >
      {#if r.state === "connecting"}
        <div
          class="size-5 animate-spin rounded-full border-2 border-hairline-strong border-t-cyan"
        ></div>
        {#if mainFeed}<span class="readout">Conectando…</span>{/if}
      {:else}
        <VideoOff class="{mainFeed ? 'size-7' : 'size-5'} text-ink-mute" />
        <div class="flex flex-col gap-2">
          <!-- The small tiles state the condition; only the main panel explains
               it. Repeating the full sentence in all three read as three
               separate failures rather than one. -->
          <span class="stamp-quiet {mainFeed ? 'text-lg' : 'text-sm'} text-ink">
            {#if r.detail !== "cert"}
              Sin señal
            {:else if mainFeed}
              El navegador bloquea esta cámara
            {:else}
              Certificado pendiente
            {/if}
          </span>
          {#if mainFeed}
            <p class="max-w-[46ch] text-sm leading-relaxed text-ink-mute">
              {#if r.detail === "cert"}
                Cada cámara sirve su video con un certificado propio. Abrí los
                tres puertos una vez, aceptá el certificado en cada uno, y volvé
                acá.
              {:else}
                No hay ningún servidor de cámara respondiendo en {host ||
                  "este host"}. Revisá que el simulador esté corriendo.
              {/if}
            </p>
          {/if}
        </div>
        <div class="flex flex-wrap items-center justify-center gap-2">
          {#if r.detail === "cert"}
            <!-- All three ports on the main panel: accepting only :60001 gets
                 the head feed back and leaves both wrists dead with no
                 explanation, which reads as a second, unrelated failure. -->
            {#each mainFeed ? cameras : [{ id, port, label: "" }] as cam (cam.port)}
              <a
                href="https://{host}:{cam.port}"
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex min-h-9 items-center gap-1.5 tile-interactive px-3 py-1.5 text-sm text-ink"
              >
                :{cam.port}
                <ExternalLink class="size-3.5" />
              </a>
            {/each}
          {/if}
          <button
            type="button"
            onclick={() => (mainFeed ? reconnectAll() : start(id, port))}
            class="inline-flex min-h-9 items-center gap-1.5 tile-interactive px-3 py-1.5 text-sm text-ink"
          >
            <RotateCw class="size-3.5" /> Reintentar
          </button>
        </div>
      {/if}
    </div>
  {/if}
{/snippet}

<div class="flex h-full min-h-0 flex-col gap-4 pb-2 lg:flex-row">
  <!-- Main feed -->
  <section
    class="flex min-h-[380px] min-w-0 flex-1 flex-col overflow-hidden panel"
  >
    <!-- Toolbar -->
    <div
      class="flex items-center gap-2 border-b border-hairline bg-primary-foreground px-4 py-3"
    >
      <Button
        variant="outline"
        size="sm"
        onclick={reconnectAll}
        class="h-auto gap-2 rounded-full border-hairline bg-wash px-3.5 py-1.5 font-mono text-2xs tracking-[0.12em] text-ink uppercase hover:border-accent-edge hover:bg-wash-hover hover:text-ink"
      >
        <RotateCw class="size-3" /> Reconectar
      </Button>
      <span class="ms-auto readout">
        {robotMode ? "G1 · D435i" : "SIM · teleimager"}
      </span>
      <Button
        variant="outline"
        size="icon"
        aria-label="Pantalla completa"
        onclick={() =>
          (robotMode ? canvasEl : videoEls.head)?.requestFullscreen?.()}
        class="size-7 rounded-full border-hairline bg-wash text-ink hover:border-accent-edge hover:bg-wash-hover hover:text-ink"
      >
        <Maximize2 class="size-3.5" />
      </Button>
    </div>

    <!-- Feed canvas. Deliberately bare: this used to draw a vanishing-point
         "floor" and a HUD crosshair over it. Neither corresponded to anything —
         the crosshair aimed at nothing and the floor was a picture of a room
         that isn't there — so a dead camera looked like a working one. -->
    <div
      bind:this={canvasEl}
      class="relative min-h-0 flex-1 overflow-hidden bg-trench"
    >
      {#if robotMode}
        {@render robotFeed()}
      {:else}
        {@render feed("head", 60001, true)}
      {/if}

      <!-- top-left overlay -->
      <div
        class="absolute top-4 left-4 z-30 flex flex-col items-start gap-1 font-mono text-2xs tracking-wide"
      >
        <Badge
          variant="outline"
          class="gap-1.5 rounded-full px-2 py-0.5 font-mono text-2xs tracking-[0.1em] uppercase {mainState ===
          'live'
            ? 'border-cyan/30 bg-cyan/10 text-cyan'
            : mainState === 'down'
              ? 'border-danger/30 bg-danger/10 text-danger-soft'
              : 'border-warn/30 bg-warn/10 text-warn'}"
        >
          <span
            class="size-1.5 rounded-full {mainState === 'live'
              ? 'bg-cyan shadow-[0_0_8px_var(--c3-cyan-hot)]'
              : mainState === 'down'
                ? 'bg-danger'
                : 'bg-warn'}"
          ></span>
          {mainLabel}
        </Badge>
        {#if robotMode}
          <span class="text-ink">Frontal · D435i color</span>
          <span class="text-ink-mute">{robotBase}</span>
        {:else}
          <span class="text-ink">Cabeza · ojo izq.</span>
          <span class="text-ink-mute">{host || "…"}:60001</span>
        {/if}
      </div>
      <!-- footer -->
      <div
        class="absolute inset-x-4 bottom-4 z-30 flex items-center justify-between readout"
      >
        {#if robotMode}
          <!-- Measured, not configured: the container decimates to 5 Hz by
               default and a loaded Jetson delivers what it delivers. -->
          <span>
            {robot.status?.stream_width && robot.status?.stream_height
              ? `${robot.status.stream_width}×${robot.status.stream_height}`
              : "—"} · {robot.fps === null ? "—" : robot.fps.toFixed(1)} fps
          </span>
          <span>MJPEG · c3po-perception-vision</span>
        {:else}
          <span>CÁMARAS · {liveCount}/3 en vivo</span>
          <span>WEBRTC · H.264</span>
        {/if}
      </div>
    </div>
  </section>

  <!-- Other cameras -->
  <aside class="flex shrink-0 flex-col gap-4 panel p-4 lg:w-[360px]">
    <div class="flex items-center justify-between">
      <span class="eyebrow">{robotMode ? "Esta cámara" : "Otras cámaras"}</span>
      <span class="readout">{robotMode ? 1 : wrists.length}</span>
    </div>
    {#if robotMode}
      <!-- The G1 has ONE camera and the console should say so plainly. Earlier
           versions of this page showed head + two wrist tiles because the SIM
           has three; on this robot the wrist and chest cameras are not "offline",
           they are not fitted (docs/ROBOT-HARDWARE.md §6). Three permanently
           dead tiles read as three broken cameras. -->
      <dl class="flex flex-col gap-2 text-sm">
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Estado</dt>
          <dd class="text-ink">
            {robot.state === "live"
              ? "en vivo"
              : robot.state === "stale"
                ? "congelado"
                : robot.state === "connecting"
                  ? "conectando"
                  : "sin conexión"}
          </dd>
        </div>
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Último cuadro</dt>
          <dd class="text-ink">
            {robot.status?.frame_age_s === null ||
            robot.status?.frame_age_s === undefined
              ? "—"
              : `hace ${robot.status.frame_age_s.toFixed(2)} s`}
          </dd>
        </div>
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Cuadros servidos</dt>
          <dd class="text-ink">{robot.status?.frames ?? "—"}</dd>
        </div>
        <div class="flex items-baseline justify-between gap-3">
          <dt class="text-ink-mute">Sensor</dt>
          <dd class="text-ink">
            {robot.status?.width && robot.status?.height
              ? `${robot.status.width}×${robot.status.height}`
              : "—"}
          </dd>
        </div>
      </dl>
      <p class="readout leading-relaxed">
        El G1 tiene una sola cámara: el nodo de color de la RealSense D435i. La
        imagen viene del contenedor de visión de <code class="font-mono"
          >apps/perception</code
        >, que es el proceso que tiene el
        <code class="font-mono">/dev/video</code> — por eso no hay una segunda fuente
        que mostrar acá.
      </p>
    {:else}
      <!-- Single column on phones: side by side, the camera label and the
         "sin señal" overlay collide in a ~180px-wide tile. -->
      <div
        class="grid min-h-0 flex-1 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-1"
      >
        {#each wrists as cam (cam.id)}
          <div
            class="relative aspect-video min-h-0 overflow-hidden rounded-lg border border-hairline bg-trench lg:aspect-auto lg:flex-1"
          >
            {@render feed(cam.id, cam.port, false)}
            <span
              class="absolute top-2 left-2.5 z-30 flex items-center gap-1.5 font-mono text-2xs tracking-wide uppercase {rt[
                cam.id
              ].state === 'live'
                ? 'text-cyan'
                : 'text-ink-mute'}"
            >
              <span
                class="size-1.5 rounded-full {rt[cam.id].state === 'live'
                  ? 'bg-cyan shadow-[0_0_8px_var(--c3-cyan-hot)]'
                  : rt[cam.id].state === 'connecting'
                    ? 'bg-warn'
                    : 'bg-danger'}"
              ></span>
              {cam.label}
            </span>
          </div>
        {/each}
      </div>
      <p class="readout leading-relaxed">
        Servidores teleimager en {host || "este host"}:60001–60003. El navegador
        debe estar en la red del sim y aceptar el certificado de cada puerto.
      </p>
    {/if}
  </aside>
</div>
