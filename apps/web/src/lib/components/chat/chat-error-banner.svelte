<script lang="ts">
  /*
   * A failed agent turn, and the only recoveries that are safe.
   *
   * "Reintentar" is `chat.regenerate()`: it drops the failed reply and asks the
   * agent the same question from scratch. That is harmless when nothing
   * reached the robot, and dangerous when a skill already ran — the agent may
   * well walk it a second time. So when the failed turn dispatched anything
   * (`planRecovery`), the banner names those skills and offers "Continuar"
   * (keeps the partial reply, tells the agent what already ran); re-running
   * the whole turn is still possible, behind an explicit, inline confirmation.
   * Inline rather than a dialog: a modal would sit on top of the header's
   * e-stop at exactly the moment the robot may be moving.
   *
   * Only the MESSAGE is the alert. The whole Callout used to be role="alert",
   * buttons included, so opening or cancelling the inline confirmation
   * re-read the entire banner. The page re-keys this component per error, so
   * each new failure still mounts a fresh alert and is announced once.
   */
  import { RotateCw, StepForward, Plus } from "@lucide/svelte";
  import { resolve } from "$app/paths";
  import { Button } from "$lib/components/ui/button/index.js";
  import Callout from "$lib/components/callout.svelte";
  import type { ChatErrorView } from "$lib/chat/chat-error";
  import type { RecoveryPlan } from "$lib/chat/turn";

  let {
    view,
    plan,
    onretry,
    oncontinue,
  }: {
    view: ChatErrorView;
    plan: RecoveryPlan;
    onretry: () => void;
    oncontinue: (plan: Extract<RecoveryPlan, { kind: "continue" }>) => void;
  } = $props();

  let confirmingRetry = $state(false);

  const skills = $derived(
    plan.kind === "continue" ? [...plan.ran, ...plan.maybe] : [],
  );
</script>

<Callout tone="danger" live={false}>
  <div class="flex flex-col gap-2">
    <div role="alert" class="flex flex-col gap-0.5">
      <p class="font-medium text-danger-soft">{view.lead}</p>
      {#if view.hint}
        <p>{view.hint}</p>
      {/if}
    </div>

    {#if plan.kind === "continue"}
      <p class="text-ink">
        {#if plan.ran.length > 0}
          Ya se ejecutaron en el robot:
          <span class="font-mono text-xs">{plan.ran.join(", ")}</span>.
        {/if}
        {#if plan.maybe.length > 0}
          Puede que se hayan ejecutado (no llegó la respuesta):
          <span class="font-mono text-xs">{plan.maybe.join(", ")}</span>.
        {/if}
        Reintentar desde cero podría repetirlas.
      </p>
    {/if}

    <!-- The raw cause can be 600 characters of gateway HTML or a stack line;
         capped and scrollable so it never pushes the composer off a phone
         screen. A scrolling region must be reachable by keyboard, hence the
         tabindex and the name. -->
    {#if view.cause}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <div
        role="region"
        aria-label="Detalle técnico del error"
        tabindex="0"
        class="max-h-24 overflow-y-auto rounded-sm focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
      >
        <!-- One line on purpose: pre-wrap would show the template's spaces. -->
        <p class="readout break-words whitespace-pre-wrap">
          <span lang="en">{view.cause}</span>
        </p>
      </div>
    {/if}

    <div class="flex flex-wrap items-center gap-2 pt-1">
      {#if view.suggestNewChat}
        <Button
          href={resolve("/chat")}
          variant="outline"
          size="sm"
          class="gap-1.5"
        >
          <Plus class="size-3.5" aria-hidden="true" />
          Nuevo chat
        </Button>
      {:else if view.retryable && plan.kind === "retry"}
        <Button variant="outline" size="sm" class="gap-1.5" onclick={onretry}>
          <RotateCw class="size-3.5" aria-hidden="true" />
          Reintentar
        </Button>
      {:else if view.retryable && plan.kind === "continue"}
        <Button
          variant="outline"
          size="sm"
          class="gap-1.5"
          onclick={() => oncontinue(plan)}
        >
          <StepForward class="size-3.5" aria-hidden="true" />
          Continuar
        </Button>
        {#if confirmingRetry}
          <span class="text-xs text-ink">
            ¿Repetir el turno completo? Puede volver a ejecutar
            <span class="font-mono">{skills.join(", ")}</span>.
          </span>
          <Button
            variant="destructive"
            size="sm"
            onclick={() => {
              confirmingRetry = false;
              onretry();
            }}
          >
            Sí, reintentar
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onclick={() => (confirmingRetry = false)}
          >
            Cancelar
          </Button>
        {:else}
          <Button
            variant="ghost"
            size="sm"
            class="gap-1.5 text-ink-dim"
            onclick={() => (confirmingRetry = true)}
          >
            <RotateCw class="size-3.5" aria-hidden="true" />
            Reintentar desde cero…
          </Button>
        {/if}
      {/if}
    </div>
  </div>
</Callout>
