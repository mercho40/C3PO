<!--
  The real robot's camera picture, with the truth about it.

  Presentational: the PAGE owns the connection (`connectRobotCamera`) and hands
  this its state; /live-camera and the /vr-control mirror both render it, so the
  two can no longer disagree about what a dark feed means. The mirror used to
  re-implement this worse — a raw "network" token, an empty dimmed <img>
  labelled "congelada" for a camera that never sent a frame, no `onerror`.

  WHAT THIS MUST NEVER DO is show a picture that is not live as if it were. An
  <img> on an MJPEG stream holds its last frame forever with no event, so the
  badge comes from /status (via `state`), never from the image. And when the
  image itself fails while /status still says live, the blank canvas is NOT
  "EN VIVO": it is treated as not live, says so, and retries on its own.

  That image failure is known only here (the <img> is here), so it is handed
  back to the page through `bind:failedUrl` — /live-camera's side panel said
  "en vivo" and kept the snapshot button enabled over a picture this view was
  labelling SIN SEÑAL. The page derives `failedUrl === url` exactly as `broken`
  is derived below; pages that do not bind it lose nothing.

  Copy lives in `#lib/robot/camera-copy.ts` (tested), not here.
-->
<script lang="ts">
  import { onDestroy } from "svelte";
  import { browser } from "$app/env";
  import { RotateCw } from "@lucide/svelte";
  import CamOverlay from "#lib/components/robot/cam-overlay.svelte";
  import type {
    RobotCamState,
    RobotCamStatus,
  } from "#lib/robot/mjpeg-camera.js";
  import {
    CAM_BADGE_LABEL,
    CAM_BADGE_TONE,
    SOURCE_LABEL,
    describeRobotCam,
  } from "#lib/robot/camera-copy.js";
  import { cn } from "#lib/utils.js";

  // `state` is the prop's public name (the contract /vr-control imports); the
  // local binding is renamed because a variable called `state` would collide
  // with the `$state` rune in this file.
  let {
    state: camState,
    detail,
    url,
    status = null,
    onreconnect,
    class: className,
    compact = false,
    failedUrl = $bindable(null),
  }: {
    state: RobotCamState;
    detail: string | null;
    /** The stream URL to mount, or null to show no picture (e.g. tab hidden). */
    url: string | null;
    status?: RobotCamStatus | null;
    /** Reopen the stream. Also called on its own after an <img> error. */
    onreconnect?: () => void;
    class?: string;
    /**
     * Smaller type and no source line, for an embedded mirror — the /vr-control
     * headset view, so its controls keep 48 px hand-ray targets.
     */
    compact?: boolean;
    /**
     * Out (bindable): the stream URL the <img> failed to load, or null. The
     * picture is broken while this equals `url`.
     */
    failedUrl?: string | null;
  } = $props();

  // Keyed to the URL rather than a boolean, so a reconnect — which always
  // hands out a fresh URL — clears it without an effect writing state back.
  const broken = $derived(url !== null && failedUrl === url);

  // Backoff for automatic retries after an image error: a source that keeps
  // failing must not be hammered at 2 Hz, and one that recovers should be
  // back within a couple of seconds.
  let failures = 0;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  function onImgError() {
    if (!url) return;
    failedUrl = url;
    failures += 1;
    const delay = Math.min(10_000, 1500 * 2 ** (failures - 1));
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      // Nobody is watching a hidden tab, and reopening an MJPEG stream there
      // pulls up to 1920x1080 over the robot's Wi-Fi for no one (the page
      // stops the camera on hide for exactly that reason; a retry here used
      // to start it again behind its back). Deferred to the return instead.
      if (document.visibilityState === "hidden") return;
      onreconnect?.();
    }, delay);
  }

  // Back from hidden with a retry still owed: take it now. If the page
  // reopened the stream on its own (a fresh URL), the picture is no longer
  // broken and there is nothing to do.
  function onvisibilitychange() {
    if (document.visibilityState === "visible" && broken && !retryTimer) {
      onreconnect?.();
    }
  }

  function onImgLoad() {
    failures = 0;
  }

  onDestroy(() => {
    if (retryTimer) clearTimeout(retryTimer);
  });

  const pageProtocol = browser ? location.protocol : null;
  const pageOrigin = browser ? location.origin : null;

  const copy = $derived(
    describeRobotCam({
      state: camState,
      detail,
      status,
      url,
      broken,
      pageProtocol,
      pageOrigin,
    }),
  );
  const tone = $derived(CAM_BADGE_TONE[copy.badge]);
  const source = $derived(
    status?.source && status.source !== "none" ? status.source : null,
  );
</script>

<svelte:document {onvisibilitychange} />

<div class={cn("relative size-full overflow-hidden bg-trench", className)}>
  <!-- object-CONTAIN, not cover: the camera's aspect is not the panel's, and
       cropping a robot's field of view to fill a box hides obstacles the robot
       can actually see. Letterboxing is the honest framing. -->
  {#if url && !broken}
    <img
      src={url}
      alt="Cámara del robot"
      onerror={onImgError}
      onload={onImgLoad}
      class="absolute inset-0 z-10 size-full object-contain"
    />
  {/if}

  {#if copy.overlay}
    <CamOverlay overlay={copy.overlay} {compact}>
      {#snippet actions()}
        {#if onreconnect && copy.overlay?.kind === "message"}
          <button
            type="button"
            onclick={() => onreconnect?.()}
            class={[
              "inline-flex items-center gap-1.5 tile-interactive text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none",
              // Compact is the in-headset mirror (/vr-control on a Quest):
              // a hand ray needs a 48 px target, more than a mouse does.
              compact
                ? "min-h-12 min-w-12 px-4 py-2 text-sm"
                : "min-h-9 px-3 py-1.5 text-sm",
            ]}
          >
            <RotateCw class="size-3.5" aria-hidden="true" /> Reconectar
          </button>
        {/if}
      {/snippet}
    </CamOverlay>
  {/if}

  <div class="absolute top-3 left-3 z-30 flex flex-col items-start gap-1">
    <span
      role="status"
      class="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-2xs tracking-[0.1em] uppercase {tone.text}"
    >
      <span class="size-1.5 rounded-full {tone.dot}" aria-hidden="true"></span>
      {CAM_BADGE_LABEL[copy.badge]}
    </span>
    {#if source && !compact}
      <span class="readout text-ink-dim">Fuente · {SOURCE_LABEL[source]}</span>
    {/if}
  </div>
</div>
