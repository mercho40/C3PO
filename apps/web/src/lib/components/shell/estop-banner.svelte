<script lang="ts">
  /*
   * What the e-stop could not fit in one word — rendered once, under the topbar.
   *
   * The button says "Sin puente" or "Sin confirmar"; this says what to DO about
   * it ("Usá la parada física"). Those instructions used to exist only as sr-only
   * text inside the button, so a sighted operator facing a red "SIN PUENTE" was
   * never told that the order had not gone out. On a phone the button shows an
   * even shorter word, which makes this the only place the full sentence lives.
   *
   * It is also the ONE announcer for the press. /vr-control mounts a second
   * StopButton inside the headset's dom-overlay; if each button carried its own
   * live regions, every press would be announced twice.
   *
   * - polite: "Parando…" / "Detenido" — a persistent sr-only status region, so
   *   it exists before its text changes (live regions inserted along with their
   *   content are not reliably announced).
   * - warn: the same polite region, plus a visible amber notice.
   * - alert: a visible danger Callout, role=alert, until the operator decides.
   */
  import { X } from "@lucide/svelte";
  import Callout from "#lib/components/callout.svelte";
  import { estop } from "#lib/robot/estop.svelte.js";
  import { estopMessage } from "#lib/robot/estop.js";

  const message = $derived(
    estopMessage(estop.phase, estop.outcome?.code ?? null),
  );
  const politeText = $derived(
    message && message.level !== "alert"
      ? `${message.title}. ${message.body}`
      : "",
  );
  const detail = $derived(estop.outcome?.detail ?? null);
  // The classifier's own hint for an unreachable bridge is Spanish; anything
  // else in `detail` is the bridge or the network talking, verbatim, in English.
  const detailLang = $derived(
    estop.outcome?.code === "bridge_unavailable" ? undefined : "en",
  );
</script>

<p role="status" class="sr-only">{politeText}</p>

{#if message && message.level !== "polite"}
  <Callout
    tone={message.level === "alert" ? "danger" : "warn"}
    title={message.title}
    live={message.level === "alert"}
    class="shrink-0"
  >
    {message.body}
    {#if detail && message.level === "alert"}
      <span class="mt-1 block readout break-words" lang={detailLang}
        >{detail}</span
      >
    {/if}
    {#snippet action()}
      <button
        type="button"
        onclick={() => estop.dismiss()}
        class="inline-flex size-8 items-center justify-center rounded-md text-ink-mute transition-colors hover:bg-wash-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
        title="Descartar este aviso"
      >
        <X class="size-4" aria-hidden="true" />
        <span class="sr-only">Descartar el aviso de parada</span>
      </button>
    {/snippet}
  </Callout>
{/if}
