<!--
  Which preset is running, while the others wait for it.

  The lock used to be invisible: every other tile stayed enabled-looking and a
  tap on one did nothing — no disabled state, no message, no outcome — for as
  long as a gesture took (up to ~15 s plus the hold before the auto-release).
-->
<script lang="ts">
  import { Loader2 } from "@lucide/svelte";
  import { BRING_UP_STEPS, SAFE_STEP } from "$lib/robot/vr-logic";

  let {
    lock,
    announce = true,
  }: {
    lock: { key: string; label: string } | null;
    /** Two panels show this line; only one should speak it. */
    announce?: boolean;
  } = $props();

  const safeLabel =
    BRING_UP_STEPS.find((s) => s.name === SAFE_STEP)?.label ?? SAFE_STEP;
</script>

<p
  role={announce ? "status" : undefined}
  class="flex min-h-5 items-center gap-1.5 text-xs text-ink-mute"
>
  {#if lock}
    <Loader2
      class="size-3.5 animate-spin motion-reduce:animate-none"
      aria-hidden="true"
    />
    <span>
      Esperando a «{lock.label}»…
      {#if lock.key !== SAFE_STEP}
        «{safeLabel}» sigue disponible.
      {/if}
    </span>
  {/if}
</p>
