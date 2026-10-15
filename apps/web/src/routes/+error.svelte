<script lang="ts">
  /*
   * The public error page: unknown URLs, and anything that fails above the
   * console shell (including the protected layout's own load).
   *
   * This used to be SvelteKit's built-in page — a white, unstyled "404 Not
   * Found" or "500 Internal Error" in English, the one screen in the app that
   * ignored both the design system and the language. An old /voice-control
   * bookmark landed there with no way back.
   */
  import { page } from "$app/state";
  import { resolve } from "$app/paths";

  const notFound = $derived(page.status === 404);
  const GENERIC = new Set(["Not Found", "Internal Error", "Error"]);
  const message = $derived(
    page.error?.message && !GENERIC.has(page.error.message)
      ? page.error.message
      : null,
  );
  const signedIn = $derived(Boolean(page.data.user));
  const title = $derived(
    notFound ? "No encontramos esa página" : "Algo salió mal",
  );
</script>

<svelte:head>
  <title>{notFound ? "Página no encontrada" : "Error"} · C3PO</title>
</svelte:head>

<div
  class="relative flex min-h-svh items-center justify-center bg-background ambient px-4 py-10 font-display"
>
  <main class="flex w-full max-w-md flex-col items-center gap-7">
    <a
      href={resolve("/")}
      class="rounded-lg focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
    >
      <img
        src="/logo.svg"
        alt=""
        width="44"
        height="44"
        class="size-11 object-contain glow-cyan"
      />
      <span class="sr-only">C3PO — inicio</span>
    </a>

    <section
      class="flex w-full flex-col gap-4 panel-hero p-7 text-center"
      aria-labelledby="error-title"
    >
      <p class="readout">Error {page.status}</p>
      <h1 id="error-title" class="stamp text-xl text-ink">{title}</h1>
      <p class="text-sm text-ink-dim">
        {#if notFound}
          La dirección no corresponde a ninguna página de C3PO. Revisá el enlace
          o volvé al inicio.
        {:else}
          No pudimos cargar esta página. Si se repite, puede que el servidor no
          esté respondiendo.
        {/if}
      </p>
      {#if message}
        <!-- `lang="en"`: an exception's own message, in English — read with
             Spanish rules it is gibberish. -->
        <p class="tile px-3 py-2 readout break-words" lang="en">{message}</p>
      {/if}
      <div class="flex flex-wrap items-center justify-center gap-2.5 pt-1">
        <a
          href={resolve(signedIn ? "dashboard" : "/")}
          class="inline-flex h-10 items-center rounded-lg cta px-4 text-sm font-medium focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
        >
          {signedIn ? "Ir a la consola" : "Volver al inicio"}
        </a>
        {#if !signedIn}
          <a
            href={resolve("login")}
            class="inline-flex h-10 items-center tile-interactive px-4 text-sm text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
          >
            Ingresar
          </a>
        {/if}
      </div>
    </section>
  </main>
</div>
