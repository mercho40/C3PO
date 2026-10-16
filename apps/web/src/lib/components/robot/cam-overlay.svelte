<!--
  The overlay a camera panel shows when its picture is not live.

  One renderer for the robot's MJPEG feed and the simulator's WebRTC feeds, so
  "congelado", "sin señal" and "conectando" look and read the same whichever
  robot the console is attached to. The words come from
  `#lib/robot/camera-copy.ts`; this only lays them out.
-->
<script lang="ts">
  import type { Snippet } from "svelte";
  import { VideoOff } from "@lucide/svelte";
  import type { CamOverlay } from "#lib/robot/camera-copy.js";

  let {
    overlay,
    compact = false,
    actions,
  }: {
    overlay: CamOverlay;
    /** Title and actions only, smaller — for small tiles and mirrors. */
    compact?: boolean;
    /** Buttons that resolve the condition (reconnect, certificate links). */
    actions?: Snippet;
  } = $props();
</script>

{#if overlay.kind === "connecting"}
  <div
    class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-trench"
  >
    <div
      class="size-5 animate-spin rounded-full border-2 border-hairline-strong border-t-peri motion-reduce:animate-none"
      aria-hidden="true"
    ></div>
    {#if !compact}<span class="readout">Conectando…</span>{/if}
  </div>
{:else}
  <!-- Scrolls when the message outgrows the box. On a phone the 16:9 canvas
       is ~280 px tall and the longer messages (CORS: a sentence, two
       literals, a button) did not fit: the button landed on the footer
       readouts and the title under the state chip.

       Centred with `my-auto` on the inner column rather than justify-center
       on the scroller: justify-center pushes overflowing content above the
       scroll origin, where it can never be scrolled to. The vertical padding
       keeps the message clear of the corner chips the page lays over the
       canvas (the state badge top-left, the readouts along the bottom). -->
  <div
    class={[
      "absolute inset-0 z-20 flex flex-col overflow-y-auto overscroll-contain text-center",
      overlay.seeThrough ? "bg-trench/60 backdrop-blur-[1px]" : "bg-trench",
    ]}
  >
    <div
      class={[
        "my-auto flex flex-col items-center",
        compact ? "gap-2 px-3 py-8" : "gap-3 px-4 py-12 sm:px-5",
      ]}
    >
      {#if !compact}
        <VideoOff class="size-7 shrink-0 text-ink-mute" aria-hidden="true" />
      {/if}
      <div class="flex flex-col gap-2">
        <span
          class={[
            "stamp-quiet text-ink",
            compact ? "text-sm" : "text-base sm:text-lg",
          ]}
        >
          {overlay.title}
        </span>
        {#if !compact || overlay.body.length > 0}
          <p
            class={[
              "mx-auto max-w-[52ch] leading-relaxed text-ink-mute",
              compact ? "text-xs" : "text-sm",
            ]}
          >
            {#each overlay.body as part, i (i)}
              {#if typeof part === "string"}{part}{:else}<code
                  class="font-mono text-2xs text-ink-dim">{part.code}</code
                >{/if}
            {/each}
          </p>
        {/if}
        {#if overlay.bridgeEs}
          <!-- A server sentence we know, in Spanish first (camera-copy.ts). -->
          <p
            class={[
              "mx-auto max-w-[60ch] leading-relaxed text-ink-dim",
              compact ? "text-xs" : "text-sm",
            ]}
          >
            {overlay.bridgeEs}
          </p>
        {/if}
        {#if overlay.bridgeText}
          <!-- The server's own sentence. Kept verbatim (and marked English so a
               screen reader pronounces it as such) because it names the actual
               cause — "somebody took the device" — better than any paraphrase;
               under the Spanish when there is one, so it can still be grepped. -->
          <p
            class={[
              "mx-auto max-w-[60ch] readout leading-relaxed break-words",
              !overlay.bridgeEs && "text-ink-dim",
            ]}
          >
            Servidor: <span lang="en">{overlay.bridgeText}</span>
          </p>
        {/if}
      </div>
      {#if actions}
        <div class="flex flex-wrap items-center justify-center gap-2">
          {@render actions()}
        </div>
      {/if}
    </div>
  </div>
{/if}
