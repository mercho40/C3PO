<!--
  The robot on the ground: grid, trail, marker and (optionally) Nav2's costmap,
  in ONE SVG whose user units are metres.

  Shared by the dashboard's mini-map and /live-map, so a geometry or staleness
  fix lands once. The two used to be separate copies that had already drifted
  (10 % grid vs 20 %, different dash lengths, only one had a heading wedge),
  and both projected x and y to percentages of width and height with
  `preserveAspectRatio="none"` — two scales on any non-square canvas.

  Geometry rules live in `#lib/robot/map-geometry.ts` (tested): one
  pixels-per-metre scale for both axes, REP-103 frame (+x right, +y up,
  yaw counter-clockwise), world (x, y) drawn at SVG (x, −y). Things that must
  keep a constant on-screen size (the marker, stroke widths) are divided by
  `ppm` or use `vector-effect: non-scaling-stroke`.

  Presentational: the page owns the view (`center`, `span`) and decides what
  "follow" means; this reports drags, wheels and pinches through
  `onviewchange` and draws whatever it is told.
-->
<script lang="ts">
  import type { Snippet } from "svelte";
  import { ArrowRight } from "@lucide/svelte";
  import type { PoseXY } from "#lib/robot/live-state.svelte.js";
  import type { CostmapPlacement } from "#lib/robot/costmap.svelte.js";
  import { LOAD_VAR, type Load } from "#lib/robot/posture.js";
  import {
    costmapSvgRect,
    edgeMarker,
    gridLines,
    gridStep,
    headingSvgRotation,
    panCenter,
    pxPerMeter,
    toSvg,
    viewBoxOf,
    zoomAt,
    type View,
    type XY,
  } from "#lib/robot/map-geometry.js";
  import { cn } from "#lib/utils.js";

  type RobotPose = {
    x_meters_world: number;
    y_meters_world: number;
    yaw_radians_world: number;
  };

  let {
    pose,
    trail = [],
    load = "unknown",
    center,
    span,
    showHeading = true,
    stale = false,
    label = null,
    animate = false,
    costmap = null,
    interactive = false,
    onviewchange,
    onlocate,
    ppm = $bindable(0),
    ariaLabel,
    class: className,
    children,
  }: {
    /** Null draws NO marker — never a robot parked at the origin. */
    pose: RobotPose | null;
    trail?: PoseXY[];
    load?: Load;
    /** World point at the centre of the canvas, metres. */
    center: XY;
    /** Metres across the canvas's shorter side. */
    span: number;
    showHeading?: boolean;
    /** The pose is history (stale odometry, or no link): dim the marker. */
    stale?: boolean;
    /** Small tag over the marker (the unit's name). */
    label?: string | null;
    /** Ease the marker between polls. Turn off while following or zooming. */
    animate?: boolean;
    costmap?: {
      url: string;
      placement: CostmapPlacement;
      /** Old or unconfirmed: desaturated and hatched, never faded. */
      stale: boolean;
    } | null;
    /** Drag to pan, wheel/pinch to zoom, arrows and +/− from the keyboard. */
    interactive?: boolean;
    onviewchange?: (
      next: { center: XY; span: number },
      cause: "pan" | "zoom",
    ) => void;
    /** Makes the off-view arrow a button that brings the robot back. */
    onlocate?: () => void;
    /** Out: measured pixels per metre, for a scale bar drawn by the page. */
    ppm?: number;
    /**
     * What the map shows right now ("BIPED-01 en x 1.2 · y 0.4 m…"). Changes
     * with every pose. On a static canvas it labels the picture; on an
     * interactive one it is a sibling text, NOT the widget's name — a name
     * that changes while focused is re-read by NVDA on every poll.
     */
    ariaLabel: string;
    class?: string;
    /** HTML overlays (compass, controls), positioned over the canvas. */
    children?: Snippet;
  } = $props();

  const uid = $props.id();

  let width = $state(0);
  let height = $state(0);

  const view = $derived<View>({ center, span, width, height });
  const scale = $derived(pxPerMeter(view));
  $effect.pre(() => {
    // Reported out so the page's scale bar is measured against THIS canvas.
    ppm = scale;
  });

  const vb = $derived(viewBoxOf(view));
  const step = $derived(gridStep(scale));
  const grid = $derived(gridLines(view, step));

  const trailPoints = $derived(
    trail
      .map((p) => {
        const s = toSvg(p.x, p.y);
        return `${s.x},${s.y}`;
      })
      .join(" "),
  );

  const at = $derived(
    pose ? toSvg(pose.x_meters_world, pose.y_meters_world) : null,
  );
  // Only once measured: before the first layout (SSR, first paint) the
  // canvas is 0x0 and every point would count as off-view.
  const offView = $derived(
    pose && width > 0 && height > 0
      ? edgeMarker(view, { x: pose.x_meters_world, y: pose.y_meters_world }, 28)
      : null,
  );
  // The pill is kept fully on the canvas whatever edge the robot left by.
  const pill = $derived(
    offView
      ? {
          left: Math.min(Math.max(offView.left, 90), Math.max(90, width - 90)),
          top: Math.min(Math.max(offView.top, 18), Math.max(18, height - 18)),
        }
      : null,
  );

  const mapRect = $derived(costmap ? costmapSvgRect(costmap.placement) : null);

  const colour = $derived(LOAD_VAR[load]);

  // --- interaction --------------------------------------------------------

  // Plain Map, not SvelteMap: this is gesture bookkeeping that nothing
  // renders, and making it reactive would only re-run deriveds per pointer.
  const pointers = new Map<number, { x: number; y: number }>();
  let pinchDist = 0;

  function local(el: Element, x: number, y: number) {
    const r = el.getBoundingClientRect();
    return { left: x - r.left, top: y - r.top };
  }

  function onpointerdown(e: PointerEvent) {
    // The overlays are siblings of the widget, so a press on a zoom button or
    // the hint disclosure never reaches this handler — no target check needed
    // to keep their clicks from being captured as pans.
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) pinchDist = distance();
  }

  function onpointermove(e: PointerEvent) {
    const prev = pointers.get(e.pointerId);
    if (!prev) return;
    const next = { x: e.clientX, y: e.clientY };
    pointers.set(e.pointerId, next);
    if (pointers.size === 1) {
      onviewchange?.(
        {
          center: panCenter(center, next.x - prev.x, next.y - prev.y, scale),
          span,
        },
        "pan",
      );
      return;
    }
    if (pointers.size === 2) {
      const d = distance();
      if (pinchDist > 0 && d > 0) {
        const [a, b] = [...pointers.values()];
        const mid = local(
          e.currentTarget as Element,
          (a.x + b.x) / 2,
          (a.y + b.y) / 2,
        );
        onviewchange?.(zoomAt(view, pinchDist / d, mid), "zoom");
      }
      pinchDist = d;
    }
  }

  function onpointerup(e: PointerEvent) {
    pointers.delete(e.pointerId);
    pinchDist = pointers.size === 2 ? distance() : 0;
  }

  function distance(): number {
    const [a, b] = [...pointers.values()];
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
  }

  function onkeydown(e: KeyboardEvent) {
    // Keys pressed on an overlay control belong to that control.
    if (e.target !== e.currentTarget) return;
    const stepM = span * 0.1;
    const pan = (dx: number, dy: number) =>
      onviewchange?.(
        { center: { x: center.x + dx, y: center.y + dy }, span },
        "pan",
      );
    switch (e.key) {
      case "ArrowLeft":
        pan(-stepM, 0);
        break;
      case "ArrowRight":
        pan(stepM, 0);
        break;
      case "ArrowUp":
        pan(0, stepM);
        break;
      case "ArrowDown":
        pan(0, -stepM);
        break;
      case "+":
      case "=":
        onviewchange?.(zoomAt(view, 1 / 1.25), "zoom");
        break;
      case "-":
      case "_":
        onviewchange?.(zoomAt(view, 1.25), "zoom");
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  // Wheel needs a non-passive listener to stop the page scrolling under the
  // map, which an `onwheel` attribute does not guarantee.
  function wheel(node: HTMLElement) {
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const factor = Math.exp(Math.max(-1, Math.min(1, e.deltaY * 0.0015)));
      onviewchange?.(
        zoomAt(view, factor, local(node, e.clientX, e.clientY)),
        "zoom",
      );
    };
    node.addEventListener("wheel", handler, { passive: false });
    return () => node.removeEventListener("wheel", handler);
  }
</script>

<!-- Two shapes rather than conditional attributes: an interactive map is a
     keyboard-operable widget (role=application, focusable) and a static one
     is just a picture, and a11y tooling cannot verify a role that flips at
     runtime.

     In the interactive shape the widget holds ONLY the drawing. Everything
     else — the name tag, the off-view pill, the page's controls and chips —
     is a SIBLING laid over it. Nested inside role=application they were
     unreachable in a screen reader's browse mode (the application swallows
     its subtree), which hid the zoom buttons and the costmap explanation
     from exactly the users who cannot drag a map. -->
{#if interactive}
  <div
    bind:clientWidth={width}
    bind:clientHeight={height}
    class={cn("relative overflow-hidden bg-trench", className)}
    {@attach wheel}
  >
    <!-- role="application" is the ARIA role for a custom widget that takes
         its own arrow keys and pointer gestures, which is exactly this;
         Svelte's lint counts it as non-interactive, hence the ignore.

         The name and description are STATIC. What the map shows is the
         sibling paragraph after it: readable in browse mode, and not tied
         to the focused element — NVDA speaks a change to the focused
         element's name (and description), which turned the pose summary
         into an announcement on every poll for as long as the map had
         focus. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <div
      class="absolute inset-0 cursor-grab touch-none focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-cyan-hot/60 active:cursor-grabbing"
      role="application"
      aria-roledescription="mapa interactivo"
      aria-label="Mapa del robot"
      aria-describedby="{uid}-how"
      tabindex="0"
      {onkeydown}
      {onpointerdown}
      {onpointermove}
      {onpointerup}
      onpointercancel={onpointerup}
    >
      {@render drawing(false)}
    </div>
    <p id="{uid}-what" class="sr-only">{ariaLabel}</p>
    <p id="{uid}-how" class="sr-only">
      Arrastrá o usá las flechas para mover la vista; rueda, pellizco o + y −
      para acercar.
    </p>
    {@render overlays()}
  </div>
{:else}
  <div
    bind:clientWidth={width}
    bind:clientHeight={height}
    class={cn("relative overflow-hidden bg-trench", className)}
  >
    {@render drawing(true)}
    {@render overlays()}
  </div>
{/if}

{#snippet drawing(labelled: boolean)}
  <!-- Labelled only when it is the whole story (the static mini-map). Inside
       the interactive widget the description paragraph says it, once. -->
  <svg
    data-map
    class="absolute inset-0 size-full"
    viewBox="{vb.minX} {vb.minY} {vb.width} {vb.height}"
    preserveAspectRatio="xMidYMid meet"
    role={labelled ? "img" : undefined}
    aria-label={labelled ? ariaLabel : undefined}
    aria-hidden={labelled ? undefined : "true"}
  >
    <defs>
      <!-- A stale map loses its colour rather than its opacity: faded, the
           near-black lethal cells blended into the canvas and impassable
           cores read as holes. Grey still shows WHERE things were. -->
      <filter id="{uid}-desat">
        <feColorMatrix type="saturate" values="0" />
      </filter>
      <pattern
        id="{uid}-hatch"
        patternUnits="userSpaceOnUse"
        width={8 / scale}
        height={8 / scale}
        patternTransform="rotate(45)"
      >
        <line
          x1="0"
          y1="0"
          x2="0"
          y2={8 / scale}
          stroke="var(--c3-abyss)"
          stroke-width={2.5 / scale}
          stroke-opacity="0.55"
        />
      </pattern>
    </defs>

    {#if costmap && mapRect}
      <!-- A light frame under the grid's rect, so lethal cells on the edge of
           the window read as part of the map rather than as off-map space.
           The PNG is opaque and drawn at full strength: its palette is a
           safety decision (costmap_png.py), and fading it erased lethal. -->
      <rect
        x={mapRect.x - 1.5 / scale}
        y={mapRect.y - 1.5 / scale}
        width={mapRect.width + 3 / scale}
        height={mapRect.height + 3 / scale}
        fill="var(--c3-ink-mute)"
      />
      <!-- `pixelated` because a costmap cell IS the unit of knowledge:
           smoothing invents confidence between cells Nav2 never had. -->
      <image
        href={costmap.url}
        x={mapRect.x}
        y={mapRect.y}
        width={mapRect.width}
        height={mapRect.height}
        preserveAspectRatio="none"
        style="image-rendering: pixelated"
        filter={costmap.stale ? `url(#${uid}-desat)` : undefined}
      />
      {#if costmap.stale}
        <rect
          x={mapRect.x}
          y={mapRect.y}
          width={mapRect.width}
          height={mapRect.height}
          fill="url(#{uid}-hatch)"
        />
      {/if}
    {/if}

    <!-- Grid in real metres, OVER the costmap: the grid is how distance is
         read, and a map that hides it is prettier and less useful. Mid-grey
         at low opacity so it shows on the dark canvas and on white free
         space alike. The origin cross is brighter: (0,0) is where odometry
         started, the only fixed landmark there is. -->
    <g
      stroke="var(--c3-ink-mute)"
      stroke-opacity="0.2"
      stroke-width="1"
      vector-effect="non-scaling-stroke"
    >
      {#each grid.xs as x (x)}
        {#if x !== 0}
          <line
            x1={x}
            y1={vb.minY}
            x2={x}
            y2={vb.minY + vb.height}
            vector-effect="non-scaling-stroke"
          />
        {/if}
      {/each}
      {#each grid.ys as y (y)}
        {#if y !== 0}
          <line
            x1={vb.minX}
            y1={-y}
            x2={vb.minX + vb.width}
            y2={-y}
            vector-effect="non-scaling-stroke"
          />
        {/if}
      {/each}
    </g>
    <g stroke="var(--c3-ink-mute)" stroke-opacity="0.5" stroke-width="1">
      <line
        x1={vb.minX}
        y1="0"
        x2={vb.minX + vb.width}
        y2="0"
        vector-effect="non-scaling-stroke"
      />
      <line
        x1="0"
        y1={vb.minY}
        x2="0"
        y2={vb.minY + vb.height}
        vector-effect="non-scaling-stroke"
      />
    </g>

    {#if trail.length > 1}
      <polyline
        points={trailPoints}
        fill="none"
        stroke="var(--c3-peri)"
        stroke-opacity="0.7"
        stroke-width="2"
        stroke-dasharray="1 5"
        stroke-linecap="round"
        stroke-linejoin="round"
        vector-effect="non-scaling-stroke"
      />
    {/if}

    {#if pose && at}
      <!-- Positioned with a CSS transform in user units, so an easing between
           polls is possible — and a zoom, which changes only the viewBox,
           never triggers it: the marker cannot drift off the map. -->
      <g
        class={[
          "marker",
          animate &&
            "motion-safe:transition-transform motion-safe:duration-500 motion-safe:ease-out",
        ]}
        style:transform="translate({at.x}px, {at.y}px)"
        opacity={stale ? 0.45 : 1}
      >
        {#if load === "moving" && !stale}
          <circle r={22 / scale} fill="var(--c3-cyan)" fill-opacity="0.16" />
        {/if}
        <circle
          r={13 / scale}
          fill="none"
          stroke={colour}
          stroke-opacity="0.6"
          stroke-width="1"
          stroke-dasharray={stale ? "3 3" : undefined}
          vector-effect="non-scaling-stroke"
        />
        {#if showHeading}
          <!-- Drawn pointing +x and turned by −yaw: SVG rotates clockwise,
               REP-103 yaw counter-clockwise (map-geometry.ts). -->
          <polygon
            points="{8 / scale},{-4.5 / scale} {21 / scale},0 {8 / scale},{4.5 /
              scale}"
            fill={colour}
            transform="rotate({headingSvgRotation(pose.yaw_radians_world)})"
          />
        {/if}
        <circle r={9 / scale} fill="var(--c3-abyss)" fill-opacity="0.85" />
        <circle r={6 / scale} fill={colour} />
      </g>
    {/if}
  </svg>
{/snippet}

{#snippet overlays()}
  {#if pose && at && label && !offView && width > 0}
    {@const s = {
      left: width / 2 + (at.x - center.x) * scale,
      top: height / 2 + (at.y + center.y) * scale,
    }}
    <span
      class={[
        "pointer-events-none absolute -translate-x-1/2 rounded-md border bg-abyss/85 px-2 py-0.5 readout whitespace-nowrap",
        // Muted ink and a dashed edge for an old pose, never opacity: faded,
        // the 11 px tag fell below a readable contrast.
        stale
          ? "border-dashed border-hairline-strong text-ink-mute"
          : "border-hairline-strong text-ink",
      ]}
      style:left="{s.left}px"
      style:top="{s.top - 38}px"
      aria-hidden="true">{label}</span
    >
  {/if}

  {#if offView && pill}
    <!-- Off the view: say so, and which way, instead of pinning the marker to
         the edge over map cells the robot is not on. -->
    {#snippet pillBody()}
      <ArrowRight
        class="size-3.5 shrink-0"
        style="transform: rotate({offView.angleDeg}deg)"
        aria-hidden="true"
      />
      <span>fuera de vista · {offView.distanceM.toFixed(1)} m</span>
    {/snippet}
    {#if onlocate}
      <button
        type="button"
        onclick={onlocate}
        aria-label="El robot está fuera de vista, a {offView.distanceM.toFixed(
          1,
        )} m. Centrar en el robot"
        class="absolute z-10 inline-flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-full border border-hairline-strong bg-abyss/90 px-2.5 py-1 readout text-ink transition-colors hover:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
        style:left="{pill.left}px"
        style:top="{pill.top}px"
      >
        {@render pillBody()}
      </button>
    {:else}
      <span
        class="pointer-events-none absolute z-10 inline-flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-full border border-hairline-strong bg-abyss/90 px-2.5 py-1 readout text-ink"
        style:left="{pill.left}px"
        style:top="{pill.top}px"
      >
        {@render pillBody()}
      </span>
    {/if}
  {/if}

  {@render children?.()}
{/snippet}
