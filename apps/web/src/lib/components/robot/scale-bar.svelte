<!--
  A scale bar measured against the canvas it sits on.

  The old one was a fixed 80 px line labelled `100 / scale / 2` m — the
  distance from the centre to the edge, not the bar's length — so it read
  "8.3 m" over 1.7 m and the error changed with the window size. This draws a
  round length (0.5 / 1 / 2 / 5 / 10 m) at exactly `length × ppm` pixels.
-->
<script lang="ts">
  import { gridStep, niceScale } from "$lib/robot/map-geometry";

  let {
    ppm,
    maxPx = 110,
    showGrid = true,
  }: {
    /** Pixels per metre of the canvas, from PoseCanvas's `bind:ppm`. */
    ppm: number;
    maxPx?: number;
    /** Also name the grid spacing, so the grid can be counted. */
    showGrid?: boolean;
  } = $props();

  const bar = $derived(niceScale(ppm, maxPx));
  const step = $derived(gridStep(ppm));
  const fmt = (m: number) => (m < 1 ? m.toFixed(1) : String(m));
</script>

{#if ppm > 0}
  <div class="flex flex-col items-start gap-1">
    <span
      class="block h-1.5 border-x border-b border-ink-dim"
      style:width="{bar.px}px"
      aria-hidden="true"
    ></span>
    <!-- The separator is an expression, not template whitespace: Svelte
         trims the leading space inside the inner span, which rendered
         "2 m· cuadrícula". -->
    <span class="readout text-ink-dim">
      {fmt(bar.meters)} m{#if showGrid}<span class="text-ink-mute"
          >{` · cuadrícula ${fmt(step)} m`}</span
        >{/if}
    </span>
  </div>
{/if}
