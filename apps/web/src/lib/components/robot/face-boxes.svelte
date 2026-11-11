<!--
  Face boxes over the robot's camera picture: who it sees, from `/face/seen` on
  the same host as the picture (derived from the stream URL): apps/back's relay
  with the session cookie, or the bridge without one.

  Polls only while there is a picture (`url` non-null) and the tab is visible;
  boxes older than FACE_STALE_S are cleared, so a stale label never lingers.
  Logic and drawing live in `#lib/robot/face-seen.ts` (tested).
-->
<script lang="ts">
  import { ScanFace } from "@lucide/svelte";
  import { PUBLIC_API_URL } from "$app/env/public";
  import { cameraBaseFromUrl } from "#lib/robot/camera-copy.js";
  import { camCredentials } from "#lib/robot/mjpeg-camera.js";
  import {
    containRect,
    drawFaceBoxes,
    faceBoxesEnabled,
    faceSeenUrl,
    freshFaces,
    pollFaceSeen,
    setFaceBoxesEnabled,
    type FaceSeen,
  } from "#lib/robot/face-seen.js";

  let {
    img,
    url,
    compact = false,
  }: {
    /** The <img> showing the stream, for its natural size (letterboxing). */
    img: HTMLImageElement | null;
    /** The stream URL being shown, or null when there is no picture. */
    url: string | null;
    compact?: boolean;
  } = $props();

  let on = $state(faceBoxesEnabled());
  let seen = $state.raw<FaceSeen | null>(null);
  let receivedAt = $state(0);
  let now = $state(0);
  let w = $state(0);
  let h = $state(0);
  let canvas: HTMLCanvasElement | undefined = $state();

  const base = $derived(on ? cameraBaseFromUrl(url) : null);

  $effect(() => {
    if (!base) return;
    const stop = pollFaceSeen(
      faceSeenUrl(base),
      (s, at) => {
        seen = s;
        receivedAt = at;
        now = at;
      },
      {
        hidden: () => document.visibilityState === "hidden",
        credentials: camCredentials(base, PUBLIC_API_URL),
      },
    );
    // Re-check staleness between answers, so a stalled poller still clears.
    const tick = setInterval(() => (now = Date.now()), 500);
    return () => {
      stop();
      clearInterval(tick);
      seen = null;
    };
  });

  $effect(() => {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const faces = freshFaces(seen, (now - receivedAt) / 1000);
    if (!seen || !faces.length || !img) return;
    const rect = containRect(w, h, img.naturalWidth, img.naturalHeight);
    drawFaceBoxes(ctx, faces, seen.width, seen.height, rect);
  });

  function toggle() {
    on = !on;
    setFaceBoxesEnabled(on);
  }
</script>

{#if url}
  <div
    class="pointer-events-none absolute inset-0 z-20"
    bind:clientWidth={w}
    bind:clientHeight={h}
  >
    {#if on}
      <canvas bind:this={canvas} class="size-full" aria-hidden="true"></canvas>
    {/if}
  </div>
  <button
    type="button"
    onclick={toggle}
    aria-pressed={on}
    title={on ? "Ocultar caras" : "Mostrar caras"}
    aria-label={on ? "Ocultar caras" : "Mostrar caras"}
    class={[
      "absolute top-3 right-3 z-30 inline-flex items-center justify-center tile-interactive focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none",
      on ? "text-ink" : "text-ink-dim",
      compact ? "min-h-12 min-w-12" : "min-h-9 min-w-9",
    ]}
  >
    <ScanFace class="size-4" aria-hidden="true" />
  </button>
{/if}
