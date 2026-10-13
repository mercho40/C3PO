<script lang="ts" module>
  export type CalloutTone = "danger" | "warn" | "info" | "ok";
</script>

<script lang="ts">
  /*
   * The console's one inline notice.
   *
   * There were three hand-rolled banner recipes (chat, vr-control, the auth
   * forms) and about a dozen copy-pasted callout boxes inside /vr-control alone,
   * each with its own border alpha and text colour. This is the shared one.
   *
   * Convention: inline Callout for a state that PERSISTS (the bridge is down, the
   * session is read-only); a toast for the outcome of a one-shot action (a delete
   * failed, a logout failed). Danger callouts are announced (role=alert); the rest
   * are polite status regions.
   */
  import type { Snippet } from "svelte";
  import {
    CircleAlert,
    CircleCheck,
    Info,
    TriangleAlert,
  } from "@lucide/svelte";
  import { cn } from "$lib/utils";

  let {
    tone = "info",
    title,
    children,
    action,
    class: className,
    live = true,
  }: {
    tone?: CalloutTone;
    title?: string;
    children?: Snippet;
    /** Trailing control — a button or link that resolves the condition. */
    action?: Snippet;
    class?: string;
    /** Announce changes to assistive tech. Turn off for static explanations. */
    live?: boolean;
  } = $props();

  const ICON = {
    danger: CircleAlert,
    warn: TriangleAlert,
    info: Info,
    ok: CircleCheck,
  } as const;

  const TONE: Record<CalloutTone, string> = {
    danger: "border-danger/35 bg-danger/[0.06] text-danger-soft",
    warn: "border-warn/30 bg-warn/[0.06] text-warn",
    info: "border-accent-edge bg-wash text-ink-dim",
    ok: "border-ok/30 bg-ok/[0.06] text-ok",
  };

  const Icon = $derived(ICON[tone]);
  const role = $derived(
    !live ? undefined : tone === "danger" ? "alert" : "status",
  );
</script>

<!-- `flex-wrap`, so the action can take a line of its own. It used to be a
     `shrink-0` column beside the body, and a long action ("Conectá el puente
     para caminar") squeezed the body to 0px wide at 360–410px: the text that
     explained the problem vanished, the button stayed. Now, below `sm`, the
     action always sits under the body, indented to the body's edge; from `sm`
     it sits beside it — until the body would drop under ~14rem, at which point
     it wraps (the body's min-width is what the wrap is computed from). -->
<div
  {role}
  class={cn(
    "flex flex-wrap items-start gap-x-2.5 gap-y-2 rounded-lg border px-3 py-2.5 text-sm",
    TONE[tone],
    className,
  )}
>
  <Icon class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
  <div
    class="flex min-w-[min(calc(100%-1.625rem),14rem)] flex-1 flex-col gap-0.5"
  >
    {#if title}
      <p class="font-medium">{title}</p>
    {/if}
    {#if children}
      <div class="min-w-0 break-words text-ink-dim [&_a]:underline">
        {@render children()}
      </div>
    {/if}
  </div>
  {#if action}
    <div
      class="max-w-full shrink-0 basis-full ps-6.5 sm:ms-auto sm:basis-auto sm:self-center sm:ps-0"
    >
      {@render action()}
    </div>
  {/if}
</div>
