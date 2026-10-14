<!--
  What the map's colours mean.

  The costmap palette is a safety decision made in the encoder
  (apps/perception/.../costmap_png.py): unknown is a cool slate that appears
  nowhere in the warm free→lethal ramp, so "nobody looked here" can never read
  as "clear floor". A palette that careful is wasted if the console never says
  which colour is which — nothing on the map explained it before.

  The swatches use the costmap tokens in layout.css, which mirror the
  encoder's palette, so each key is exactly the colour drawn on the map.
-->
<script lang="ts">
  import { LOAD_FILL, LOAD_LABEL, type Load } from "#lib/robot/posture.js";

  let {
    costmap = true,
    load = "unknown",
  }: {
    /** Show the costmap keys (only when a costmap is drawn). */
    costmap?: boolean;
    /** The marker's current load, shown as the marker key. */
    load?: Load;
  } = $props();

  const CELLS = [
    { label: "libre", swatch: "bg-map-free" },
    { label: "inflado", swatch: "bg-map-inflated" },
    { label: "letal", swatch: "bg-map-lethal" },
    { label: "sin observar", swatch: "bg-map-unknown" },
  ];
</script>

<ul
  class="flex flex-col gap-1.5 readout text-ink-dim"
  aria-label="Leyenda del mapa"
>
  {#if costmap}
    {#each CELLS as cell (cell.label)}
      <li class="flex items-center gap-2">
        <span
          class="size-2.5 shrink-0 rounded-[2px] border border-ink-mute/60 {cell.swatch}"
          aria-hidden="true"
        ></span>
        {cell.label}
      </li>
    {/each}
  {/if}
  <li class="flex items-center gap-2">
    <span
      class="size-2.5 shrink-0 rounded-full {LOAD_FILL[load]}"
      aria-hidden="true"
    ></span>
    marcador = carga ({LOAD_LABEL[load]})
  </li>
  <li class="flex items-center gap-2">
    <span
      class="block w-2.5 shrink-0 border-t-2 border-dotted border-peri"
      aria-hidden="true"
    ></span>
    rastro = recorrido
  </li>
</ul>
