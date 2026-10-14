<script lang="ts">
  /*
   * An error inside the console — keeps the shell.
   *
   * Rendered inside the protected layout, so the sidebar, the topbar (which also
   * titles the tab as the error) and, above all, the e-stop stay on screen: a
   * page that failed to load is not a reason to lose the stop. Without this, any
   * failing page load dropped the operator onto SvelteKit's bare English "500
   * Internal Error", outside the console, with no way back.
   *
   * The heading is an h2: the topbar's h1 already names the error.
   */
  import { page } from "$app/state";
  import { refreshAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { RotateCw } from "@lucide/svelte";

  const notFound = $derived(page.status === 404);
  // SvelteKit's own defaults are English and say nothing; a message a load
  // threw on purpose (`error(503, "…")`) is worth showing.
  const GENERIC = new Set(["Not Found", "Internal Error", "Error"]);
  const message = $derived(
    page.error?.message && !GENERIC.has(page.error.message)
      ? page.error.message
      : null,
  );

  let retrying = $state(false);
  async function retry() {
    retrying = true;
    try {
      await refreshAll();
    } finally {
      retrying = false;
    }
  }
</script>

<div class="flex min-h-full items-start justify-center py-6 sm:items-center">
  <section
    class="flex w-full max-w-lg flex-col gap-4 panel p-6 sm:p-8"
    aria-labelledby="error-title"
  >
    <p class="readout">Error {page.status}</p>
    <!-- `stamp-quiet`: `stamp` is rationed to the robot's name, its posture
         and the page title — and the page title is the topbar's h1. -->
    <h2 id="error-title" class="stamp-quiet text-2xl text-ink">
      {notFound ? "No encontramos esa página" : "Algo salió mal"}
    </h2>
    <p class="text-sm text-ink-dim">
      {#if notFound}
        La dirección no corresponde a ninguna pantalla de la consola. Revisá el
        enlace o volvé al inicio.
      {:else}
        La página no se pudo cargar. El robot y la parada de emergencia no
        dependen de esta pantalla: PARAR sigue arriba a la derecha.
      {/if}
    </p>
    {#if message}
      <!-- `lang="en"`: what reaches here is an exception's own message, in
           English — read with Spanish rules it is gibberish. (A deliberate
           `error(…)` thrown for operators belongs in the copy above, in
           Spanish.) -->
      <p class="tile px-3 py-2 readout break-words" lang="en">{message}</p>
    {/if}
    <div class="flex flex-wrap items-center gap-2.5 pt-1">
      <a
        href={resolve("dashboard")}
        class="inline-flex h-10 items-center rounded-lg cta px-4 text-sm font-medium focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
      >
        Volver al inicio
      </a>
      {#if !notFound}
        <button
          type="button"
          onclick={retry}
          disabled={retrying}
          class="inline-flex h-10 items-center gap-2 tile-interactive px-4 text-sm text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-60"
        >
          <RotateCw
            class={["size-4", retrying && "motion-safe:animate-spin"]}
            aria-hidden="true"
          />
          {retrying ? "Reintentando…" : "Reintentar"}
        </button>
      {/if}
    </div>
  </section>
</div>
