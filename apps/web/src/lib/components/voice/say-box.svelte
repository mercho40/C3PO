<!--
  "Decir" — make the robot speak a sentence, directly (admins only).

  POST /skills/say/invoke is the reasoning-free path: no model in between, so
  it is admin-gated server-side like every non-safety skill. The form's limits
  come from the bridge's own schema (`readSaySpec`), and the outcome goes
  through the console's one classifier (`classifyInvoke`) — on the simulator
  `say` answers `{status: "ok", stub: true}` and only LOGS, which must not read
  as "the robot said it".

  Spanish is synthesised on the robot; English/Chinese use the firmware's two
  voices. Labelling Spanish text as English does not fail — it produces an
  English voice attempting Spanish — so the language is a visible choice.

  A FAILURE IS AN ALERT, a success is a status. An error outcome renders as a
  danger Callout (role=alert, announced when it appears); everything else is a
  line in a polite live region that takes no space while empty — the old
  always-present `min-h-5` row padded the bottom of the panel for nothing.
-->
<script lang="ts">
  import Callout from "#lib/components/callout.svelte";
  import { createApi } from "#lib/api.js";
  import { redirectToLogin } from "#lib/robot/poller.svelte.js";
  import {
    classifyInvoke,
    OUTCOME_TONE,
    type SkillOutcome,
  } from "#lib/robot/skill-outcome.js";
  import { LANGUAGE_LABEL, detailLang, readSaySpec } from "./say";

  let {
    parameters,
    error,
    conversationRunning = false,
    conversationHeld = false,
  }: {
    /** `GET /skills/say`'s `parameters`, or null when it could not be read. */
    parameters: unknown;
    /** Why the schema could not be read. */
    error: string | null;
    /** A voice session talks too; saying something now talks over it. */
    conversationRunning?: boolean;
    /** The voice status could not be read: `conversationRunning` is the last reading. */
    conversationHeld?: boolean;
  } = $props();

  const spec = $derived(readSaySpec(parameters));

  let text = $state("");
  let language = $state("");
  let sending = $state(false);
  let outcome = $state<SkillOutcome | null>(null);

  const chosenLanguage = $derived(language || spec?.defaultLanguage || "");
  const trimmed = $derived(text.trim());
  const tooLong = $derived(spec ? text.length > spec.maxLength : false);
  const canSend = $derived(
    !!spec && !sending && trimmed.length >= (spec?.minLength ?? 1) && !tooLong,
  );

  async function say(event: SubmitEvent) {
    event.preventDefault();
    if (!canSend) return;
    sending = true;
    outcome = null;
    try {
      const res = await createApi(fetch)
        .skills({ name: "say" })
        .invoke.post({
          text: trimmed,
          ...(chosenLanguage ? { language: chosenLanguage } : {}),
        });
      // A 401 is the session, not the robot: log in again and come back.
      if ((res.error?.status as number | undefined) === 401) {
        redirectToLogin("expired");
        return;
      }
      outcome = classifyInvoke(res);
      if (outcome.kind === "ok" && !outcome.simulated) text = "";
    } catch {
      outcome = classifyInvoke({ error: { status: null } });
    } finally {
      sending = false;
    }
  }
</script>

<section
  class="flex min-w-0 flex-col gap-3 panel p-5"
  aria-labelledby="say-heading"
>
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <h2 id="say-heading" class="eyebrow">Decir</h2>
    <span class="readout">Admin · directo, sin agente</span>
  </div>

  {#if error || !spec}
    <Callout tone="warn" title="No disponible">
      {error ??
        "El esquema de «say» que publica el puente cambió y la consola no lo reconoce."}
    </Callout>
  {:else}
    <form class="flex flex-col gap-3" onsubmit={say}>
      <div class="flex flex-col gap-1.5">
        <label for="say-text" class="text-sm text-ink-dim">
          Lo que el robot va a decir en voz alta por su parlante
        </label>
        <textarea
          id="say-text"
          bind:value={text}
          rows="3"
          maxlength={spec.maxLength}
          placeholder="Hola, soy C3PO."
          aria-describedby="say-count"
          class="w-full resize-y tile px-3 py-2 text-base text-ink placeholder:text-ink-mute focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
        ></textarea>
        <span
          id="say-count"
          class={["self-end readout", tooLong && "text-danger-soft"]}
        >
          {text.length} / {spec.maxLength}
        </span>
      </div>

      <!-- No gap here: the status line below adds its own margin only when it
           has something to say, so an empty region costs no height. -->
      <div class="flex flex-col">
        <div class="flex flex-wrap items-end gap-3">
          {#if spec.languages.length > 1}
            <div class="flex flex-col gap-1.5">
              <label for="say-language" class="text-sm text-ink-dim"
                >Idioma</label
              >
              <select
                id="say-language"
                value={chosenLanguage}
                onchange={(e) => (language = e.currentTarget.value)}
                class="h-10 tile px-3 text-base text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
              >
                {#each spec.languages as lang (lang)}
                  <option value={lang}>{LANGUAGE_LABEL[lang] ?? lang}</option>
                {/each}
              </select>
            </div>
          {/if}
          <button
            type="submit"
            disabled={!canSend}
            class="inline-flex h-10 items-center justify-center rounded-lg cta px-4 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
          >
            {sending ? "Enviando…" : "Decir"}
          </button>
        </div>

        <div role="status" aria-live="polite">
          {#if outcome && outcome.kind !== "error"}
            <p class="mt-3 text-sm {OUTCOME_TONE[outcome.kind]}">
              {outcome.text}{#if outcome.simulated}
                <span class="text-ink-mute">
                  · simulación: no se reprodujo audio</span
                >
              {/if}
            </p>
            {#if outcome.detail}
              <p
                class="mt-0.5 readout break-words"
                lang={detailLang(outcome.detail)}
              >
                {outcome.detail}
              </p>
            {/if}
          {/if}
        </div>
      </div>

      {#if outcome?.kind === "error"}
        <Callout tone="danger" title={outcome.text}>
          {#if outcome.detail}<span
              class="readout break-words"
              lang={detailLang(outcome.detail)}>{outcome.detail}</span
            >{/if}
        </Callout>
      {/if}

      {#if conversationRunning}
        <p class="text-xs text-ink-mute">
          {conversationHeld
            ? "Según la última lectura hay una conversación de voz activa: si sigue, lo que mandes ahora se va a superponer con sus respuestas."
            : "Hay una conversación de voz activa: lo que mandes ahora se va a superponer con sus respuestas."}
        </p>
      {/if}
    </form>
  {/if}
</section>
