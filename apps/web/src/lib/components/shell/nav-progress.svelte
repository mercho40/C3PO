<script lang="ts">
  /*
   * A thin bar along the top of the console while a navigation is loading.
   *
   * Client navigation waits for the target's server load before anything on
   * screen changes, and several loads reach the bridge (a stalled one can take
   * seconds). Nothing used to show progress, so a sidebar tap during a stall
   * looked dead and got tapped again. nav-main marks the item being loaded; this
   * says that something is.
   *
   * Appears after a short delay so instant navigations do not flash it.
   * Periwinkle, not cyan: cyan means "moving under power" and nothing else.
   */
  import { navigating } from "$app/state";
  import { navItemFor } from "#lib/nav.js";

  const target = $derived(
    navigating.to
      ? (navItemFor(navigating.to.url.pathname)?.title ?? null)
      : null,
  );
</script>

{#if navigating.to}
  <div
    class="nav-progress pointer-events-none absolute inset-x-0 top-0 z-20 h-0.5 overflow-hidden"
    role="progressbar"
    aria-label={target ? `Cargando ${target}` : "Cargando la página"}
  >
    <span class="bar"></span>
  </div>
{/if}

<style>
  .nav-progress {
    opacity: 0;
    animation: appear 150ms ease-out 120ms forwards;
    background-color: color-mix(in srgb, var(--c3-peri) 12%, transparent);
  }

  .bar {
    position: absolute;
    inset-block: 0;
    left: 0;
    width: 40%;
    background-color: var(--c3-peri);
    animation: slide 1.1s ease-in-out infinite;
  }

  @keyframes appear {
    to {
      opacity: 1;
    }
  }

  @keyframes slide {
    from {
      transform: translateX(-100%);
    }
    to {
      transform: translateX(250%);
    }
  }

  /* Reduced motion: a still bar still says "loading"; it just does not move. */
  @media (prefers-reduced-motion: reduce) {
    .nav-progress {
      animation: none;
      opacity: 1;
    }
    .bar {
      animation: none;
      width: 100%;
      opacity: 0.6;
    }
  }
</style>
