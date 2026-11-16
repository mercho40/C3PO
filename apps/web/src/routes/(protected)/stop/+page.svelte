<script lang="ts">
  /*
   * What a no-JavaScript PARAR did.
   *
   * StopButton posts here when it is pressed before the console hydrated (or
   * with JS off). The action sent the stop and classified the answer with the
   * button's own rules; this shows the banner's own copy for it. A plain GET has
   * no result, only directions to the real stop.
   *
   * Its own h1: /stop is not in `#lib/nav`, so the topbar renders none.
   */
  import { resolve } from "$app/paths";
  import Callout from "#lib/components/callout.svelte";
  import { estopMessage, STOP_HOTKEY_LABEL } from "#lib/robot/estop.js";

  let { form } = $props();

  const message = $derived(form ? estopMessage(form.phase, form.code) : null);
</script>

<div class="flex min-h-full items-start justify-center py-6 sm:items-center">
  <section
    class="flex w-full max-w-lg flex-col gap-4 panel p-6 sm:p-8"
    aria-labelledby="stop-title"
  >
    <h1 id="stop-title" class="stamp text-2xl text-ink">
      Parada de emergencia
    </h1>
    {#if message}
      <Callout
        tone={message.level === "alert"
          ? "danger"
          : message.level === "warn"
            ? "warn"
            : "info"}
        title={message.title}
      >
        {message.body}
        {#if form?.phase === "accepted"}
          <!-- Nothing on this page polls telemetry, so "Esperando que la
               telemetría lo confirme" would wait forever. -->
          <span class="mt-1 block"
            >Sin JavaScript esta página no puede confirmarlo: mirá el robot.</span
          >
        {/if}
      </Callout>
    {:else}
      <p class="text-sm text-ink-dim">
        PARAR está arriba a la derecha, en la barra de la consola, en todas las
        pantallas (también con {STOP_HOTKEY_LABEL}).
      </p>
    {/if}
    <div class="pt-1">
      <a
        href={resolve("dashboard")}
        class="inline-flex h-10 items-center rounded-lg cta px-4 text-sm font-medium focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
      >
        Volver al inicio
      </a>
    </div>
  </section>
</div>
