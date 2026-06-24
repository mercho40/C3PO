<script lang="ts">
  import { onMount } from "svelte";
  import { env } from "$env/dynamic/public";
  import { Camera, Share2, Maximize2, RotateCw } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import {
    connectSimCamera,
    type SimCamHandle,
    type SimCamState,
  } from "$lib/webrtc/sim-camera";

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

  type Runtime = { stream: MediaStream | null; state: SimCamState; detail: string };
  const blank = (): Runtime => ({ stream: null, state: "connecting", detail: "" });
  let rt = $state<Record<CamId, Runtime>>({ head: blank(), lwrist: blank(), rwrist: blank() });
  const handles: Partial<Record<CamId, SimCamHandle>> = {};

  // Default to the host serving the console (browser runs on the sim box), so no
  // config is needed in the co-located setup; override with PUBLIC_SIM_CAM_HOST.
  let host = $state("");
  let videoEls = $state<Partial<Record<CamId, HTMLVideoElement>>>({});

  function start(id: CamId, port: number) {
    handles[id]?.close();
    rt[id] = blank();
    handles[id] = connectSimCamera(host, port, {
      onStream: (stream) => (rt[id] = { ...rt[id], stream }),
      onState: (state, detail) => (rt[id] = { ...rt[id], state, detail: detail ?? "" }),
    });
  }

  function reconnectAll() {
    for (const c of cameras) start(c.id, c.port);
  }

  onMount(() => {
    host = env.PUBLIC_SIM_CAM_HOST || location.hostname;
    reconnectAll();
    return () => {
      for (const c of cameras) handles[c.id]?.close();
    };
  });

  // <video>.srcObject can only be assigned imperatively; the action also keeps a
  // ref to each <video> (keyed by camera) so the toolbar can fullscreen the head.
  function video(node: HTMLVideoElement, args: { id: CamId; stream: MediaStream | null }) {
    node.srcObject = args.stream;
    videoEls[args.id] = node;
    return {
      update: (a: { id: CamId; stream: MediaStream | null }) => (node.srcObject = a.stream),
      destroy: () => {
        if (videoEls[args.id] === node) delete videoEls[args.id];
      },
    };
  }

  const headState = $derived(rt.head.state);
  const liveCount = $derived(cameras.filter((c) => rt[c.id].state === "live").length);
</script>

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
    <div
      class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 px-4 text-center {r.stream
        ? 'bg-[#02050a]/55 backdrop-blur-[1px]'
        : ''}"
    >
      {#if r.state === "connecting"}
        <div
          class="size-5 animate-spin rounded-full border-2 border-[rgba(180,210,255,0.18)] border-t-[#9ae5f8]"
        ></div>
        {#if mainFeed}<span class="font-mono text-[10px] tracking-wide text-[#8a96ad]">Conectando…</span>{/if}
      {:else}
        <span class="font-mono text-[11px] tracking-wide text-[#ff8aa0]">
          {r.detail === "cert" ? "Certificado no aceptado" : "Sin señal"}
        </span>
        {#if r.detail === "cert"}
          <a
            href="https://{host}:{port}"
            target="_blank"
            rel="noopener noreferrer"
            class="font-mono text-[10px] text-[#9ae5f8] underline-offset-2 hover:underline"
          >
            Aceptar certificado :{port} ↗
          </a>
        {/if}
        {#if mainFeed}
          <Button
            variant="outline"
            size="sm"
            onclick={() => start(id, port)}
            class="h-auto gap-1.5 rounded-full border-[rgba(180,210,255,0.12)] bg-[rgba(180,210,255,0.03)] px-3 py-1 font-mono text-[10px] tracking-wide text-[#eaf1ff] uppercase hover:border-[rgba(159,197,255,0.25)] hover:bg-[rgba(180,210,255,0.03)] hover:text-[#eaf1ff]"
          >
            <RotateCw class="size-3" /> Reintentar
          </Button>
        {/if}
      {/if}
    </div>
  {/if}
{/snippet}

<div class="flex h-full gap-[18px] pb-2">
  <!-- Main feed -->
  <section
    class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828]"
  >
    <!-- Toolbar -->
    <div
      class="flex items-center gap-2 border-b border-[rgba(180,210,255,0.08)] bg-[#06121c] px-[18px] py-3.5"
    >
      <Button
        variant="outline"
        size="sm"
        class="h-auto gap-2 rounded-full border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] px-3.5 py-1.5 font-mono text-[10px] tracking-[0.12em] text-[#eaf1ff] uppercase hover:border-[rgba(159,197,255,0.25)] hover:bg-[rgba(180,210,255,0.03)] hover:text-[#eaf1ff]"
      >
        <Camera class="size-3" /> Captura
      </Button>
      <Button
        variant="outline"
        size="sm"
        class="h-auto gap-2 rounded-full border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] px-3.5 py-1.5 font-mono text-[10px] tracking-[0.12em] text-[#eaf1ff] uppercase hover:border-[rgba(159,197,255,0.25)] hover:bg-[rgba(180,210,255,0.03)] hover:text-[#eaf1ff]"
      >
        <Share2 class="size-3" /> Compartir
      </Button>
      <Button
        variant="outline"
        size="sm"
        onclick={reconnectAll}
        class="h-auto gap-2 rounded-full border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] px-3.5 py-1.5 font-mono text-[10px] tracking-[0.12em] text-[#eaf1ff] uppercase hover:border-[rgba(159,197,255,0.25)] hover:bg-[rgba(180,210,255,0.03)] hover:text-[#eaf1ff]"
      >
        <RotateCw class="size-3" /> Reconectar
      </Button>
      <Button
        variant="outline"
        size="icon"
        aria-label="Pantalla completa"
        onclick={() => videoEls.head?.requestFullscreen?.()}
        class="size-[26px] rounded-full border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] text-[#eaf1ff] hover:border-[rgba(159,197,255,0.25)] hover:bg-[rgba(180,210,255,0.03)] hover:text-[#eaf1ff]"
      >
        <Maximize2 class="size-3" />
      </Button>
    </div>

    <!-- Feed canvas -->
    <div class="relative min-h-0 flex-1 overflow-hidden bg-[#02050a]">
      <!-- perspective grid (shown behind / when no signal) -->
      <svg class="absolute inset-0 size-full" preserveAspectRatio="none" viewBox="0 0 758 712">
        <defs>
          <linearGradient id="cam-floor" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#1a2030" />
            <stop offset="45%" stop-color="#06121c" />
            <stop offset="100%" stop-color="#060912" />
          </linearGradient>
        </defs>
        <rect width="758" height="712" fill="url(#cam-floor)" />
        <g stroke="rgba(180,210,255,0.10)" stroke-width="1">
          {#each [0, 84, 168, 252, 336, 422, 506, 590, 674, 758] as x (x)}
            <line x1={x} y1="712" x2="379" y2="330" />
          {/each}
          {#each [340, 360, 390, 440, 520, 640] as y (y)}
            <line x1="0" y1={y} x2="758" y2={y} />
          {/each}
        </g>
      </svg>

      {@render feed("head", 60001, true)}

      <!-- crosshair -->
      <div class="absolute top-1/2 left-1/2 z-30 size-10 -translate-x-1/2 -translate-y-1/2">
        <span class="absolute top-0 left-1/2 h-3 w-px bg-[#9ae5f8]"></span>
        <span class="absolute bottom-0 left-1/2 h-3 w-px bg-[#9ae5f8]"></span>
        <span class="absolute top-1/2 left-0 h-px w-3 bg-[#9ae5f8]"></span>
        <span class="absolute top-1/2 right-0 h-px w-3 bg-[#9ae5f8]"></span>
      </div>

      <!-- top-left overlay -->
      <div
        class="font-mono absolute top-4 left-4 z-30 flex flex-col items-start gap-1 text-[11px] tracking-wide"
      >
        <Badge
          variant="outline"
          class="gap-1.5 rounded-full px-2 py-0.5 font-mono text-[10px] tracking-[0.1em] uppercase {headState ===
          'live'
            ? 'border-[rgba(126,229,255,0.3)] bg-[rgba(126,229,255,0.08)] text-[#9ae5f8]'
            : headState === 'connecting'
              ? 'border-[rgba(245,182,67,0.3)] bg-[rgba(245,182,67,0.08)] text-[#f5b643]'
              : 'border-[rgba(255,77,106,0.3)] bg-[rgba(255,77,106,0.08)] text-[#ff8aa0]'}"
        >
          <span
            class="size-1.5 rounded-full {headState === 'live'
              ? 'bg-[#9ae5f8] shadow-[0px_0px_8px_#7ee5ff]'
              : headState === 'connecting'
                ? 'bg-[#f5b643]'
                : 'bg-[#ff4d6a]'}"
          ></span>
          {headState === "live" ? "EN VIVO" : headState === "connecting" ? "Conectando" : "Sin señal"}
        </Badge>
        <span class="text-[#eaf1ff]">Cabeza · ojo izq.</span>
        <span class="text-[#8a96ad]">{host || "…"}:60001</span>
      </div>
      <!-- footer -->
      <div
        class="font-mono absolute inset-x-4 bottom-4 z-30 flex items-center justify-between text-[10px] tracking-wide text-[#8a96ad]"
      >
        <span>CÁMARAS · {liveCount}/3 en vivo</span>
        <span>WEBRTC · H.264</span>
      </div>
    </div>
  </section>

  <!-- Other cameras -->
  <aside
    class="flex w-[400px] shrink-0 flex-col gap-4 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-4"
  >
    <div class="flex items-center justify-between">
      <span class="text-[10px] font-medium tracking-[0.18em] text-[#8a96ad] uppercase">Otras cámaras</span>
      <span class="font-mono text-[10px] text-[#8a96ad]">{wrists.length}</span>
    </div>
    <div class="flex min-h-0 flex-1 flex-col gap-4">
      {#each wrists as cam (cam.id)}
        <div
          class="relative min-h-0 flex-1 overflow-hidden rounded-lg border border-[rgba(180,210,255,0.08)] bg-[#0a0e14]"
        >
          {@render feed(cam.id, cam.port, false)}
          <span
            class="font-mono absolute top-2 left-2.5 z-30 flex items-center gap-1.5 text-[9px] tracking-wide uppercase {rt[
              cam.id
            ].state === 'live'
              ? 'text-[#9ae5f8]'
              : 'text-[#8a96ad]'}"
          >
            <span
              class="size-1.5 rounded-full {rt[cam.id].state === 'live'
                ? 'bg-[#9ae5f8] shadow-[0px_0px_8px_#7ee5ff]'
                : rt[cam.id].state === 'connecting'
                  ? 'bg-[#f5b643]'
                  : 'bg-[#ff4d6a]'}"
            ></span>
            {cam.label}
          </span>
        </div>
      {/each}
    </div>
    <p class="font-mono text-[9px] leading-relaxed tracking-wide text-[#8a96ad]">
      Servidores teleimager en {host || "este host"}:60001–60003. El navegador debe estar en la
      red del sim y aceptar el certificado de cada puerto.
    </p>
  </aside>
</div>
