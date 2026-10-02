<!--
  One preset skill as a big tap target: a bring-up step, a gesture.

  Aimed at with a hand ray in the headset overlay, so it is large on purpose
  (≥ 56 px tall in every layout). Refuses taps while another preset holds the
  dispatch lock — the panel says which one — because a tap that silently does
  nothing is the failure this replaced.

  `aria-disabled` plus a guard, not `disabled`: a native disabled button drops
  keyboard focus the moment it goes busy, so the operator who just pressed it
  was thrown back to the top of the document mid-action — the same reason the
  e-stop button does it this way.

  `caveat`: the action will most likely be refused (e.g. a gesture outside a
  walk program). The tile is dimmed and says why, but stays pressable — the
  gate warns, it does not block; the firmware is the authority.

  `holdToConfirm` (catalogue dangerLevel `high`): the tile dispatches only after
  being held for HOLD_CONFIRM_MS, with a fill showing progress; letting go early
  cancels. No modal — one would cover PARAR in the headset overlay.
-->
<script lang="ts">
  import type { Component } from "svelte";
  import { onDestroy } from "svelte";
  import { Check } from "@lucide/svelte";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import OutcomeLine from "./outcome-line.svelte";
  import { LOAD_BORDER, LOAD_FILL, type Load } from "$lib/robot/posture";
  import {
    HOLD_CONFIRM_MS,
    type StampedOutcome,
    type StepMark,
  } from "$lib/robot/vr-logic";
  import { cn } from "$lib/utils";

  let {
    label,
    labelLang,
    hint = null,
    icon: Icon = null,
    badge = null,
    busy = false,
    locked = false,
    stamped = null,
    now,
    mark = null,
    markLoad = null,
    layout = "step",
    holdToConfirm = false,
    caveat = null,
    onrun,
  }: {
    label: string;
    /** Set to "en" when the label is a raw firmware token. */
    labelLang?: "en";
    hint?: string | null;
    icon?: Component | null;
    badge?: "untested" | "unknown" | null;
    busy?: boolean;
    /** Another preset holds the lock. */
    locked?: boolean;
    stamped?: StampedOutcome | null;
    now: number;
    /** Bring-up position marker, from the live posture. */
    mark?: StepMark | null;
    /** The load this step puts the body in — colours the "current" marker. */
    markLoad?: Load | null;
    layout?: "step" | "gesture" | "compact";
    /** Require a deliberate hold before dispatching (high-danger skills). */
    holdToConfirm?: boolean;
    /** Why this will probably be refused right now. Dims the tile; never blocks it. */
    caveat?: string | null;
    onrun: () => void;
  } = $props();

  const BADGE_TEXT = {
    untested: "Sin probar en real",
    unknown: "Sin verificar",
  } as const;

  const HOLD_KEYS = new Set([" ", "Enter"]);
  // A modified key is never a hold: Shift+Space is the global e-stop, and the
  // layout swallows it in the capture phase — this is the second guard.
  const plain = (e: KeyboardEvent) =>
    !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey;

  let holding = $state(false);
  let holdTimer: ReturnType<typeof setTimeout> | null = null;

  const blocked = $derived(busy || locked);

  function beginHold() {
    if (!holdToConfirm || holding || blocked) return;
    holding = true;
    holdTimer = setTimeout(() => {
      holdTimer = null;
      holding = false;
      // Another preset may have taken the lock while this one was held.
      if (!blocked) onrun();
    }, HOLD_CONFIRM_MS);
  }

  function cancelHold() {
    if (holdTimer) clearTimeout(holdTimer);
    holdTimer = null;
    holding = false;
  }

  function onclick() {
    // Held tiles dispatch from the timer, never from a click — a click is
    // exactly the brushed-past tap the hold exists to filter out.
    if (!holdToConfirm && !blocked) onrun();
  }

  function onkeydown(e: KeyboardEvent) {
    if (!holdToConfirm || !HOLD_KEYS.has(e.key) || !plain(e)) return;
    e.preventDefault();
    if (!e.repeat) beginHold();
  }

  function onkeyup(e: KeyboardEvent) {
    if (!holdToConfirm || !HOLD_KEYS.has(e.key)) return;
    e.preventDefault();
    cancelHold();
  }

  onDestroy(cancelHold);
</script>

<button
  type="button"
  aria-busy={busy}
  aria-disabled={blocked}
  {onclick}
  onpointerdown={(e) => {
    if (e.button === 0) beginHold();
  }}
  onpointerup={cancelHold}
  onpointerleave={cancelHold}
  onpointercancel={cancelHold}
  oncontextmenu={(e) => {
    // A 600 ms touch hold is long enough to open the long-press menu, which
    // cancels the pointer — the confirmation could never complete on a phone.
    if (holdToConfirm) e.preventDefault();
  }}
  {onkeydown}
  {onkeyup}
  onblur={cancelHold}
  class={cn(
    "relative flex flex-col gap-1 overflow-hidden tile-interactive p-3 text-sm text-ink focus-visible:border-accent-edge focus-visible:ring-3 focus-visible:ring-cyan-hot/50 focus-visible:outline-none aria-disabled:cursor-not-allowed aria-disabled:opacity-55 aria-disabled:hover:bg-wash",
    layout === "step" && "min-h-24 items-start justify-start text-left",
    layout === "gesture" && "min-h-24 items-center justify-center text-center",
    layout === "compact" && "min-h-14 items-start justify-center text-left",
    holdToConfirm && "touch-none select-none",
    mark === "current" && markLoad && ["border-2", LOAD_BORDER[markLoad]],
    mark === "next" && "border-accent-edge bg-wash-hover",
    // Dimmed, but well short of a blocked tile's 55 % opacity, and dashed:
    // "you can, but expect a no".
    caveat && "border-dashed text-ink-dim opacity-80",
  )}
>
  {#if holdToConfirm}
    <!-- The fill IS the confirmation: it reaches the edge exactly when the
         skill dispatches. Periwinkle, not cyan — nothing is moving yet. -->
    <span
      aria-hidden="true"
      class={[
        "pointer-events-none absolute inset-x-0 bottom-0 h-1 origin-left bg-peri motion-reduce:transition-none",
        holding ? "scale-x-100 transition-transform ease-linear" : "scale-x-0",
      ]}
      style:transition-duration={holding ? `${HOLD_CONFIRM_MS}ms` : "0ms"}
    ></span>
  {/if}
  {#if mark === "current" || mark === "done" || mark === "next"}
    <span
      class={cn(
        "inline-flex items-center gap-1.5 text-2xs font-medium tracking-[0.08em] uppercase",
        mark === "next" ? "text-ink" : "text-ink-mute",
      )}
    >
      {#if mark === "current" && markLoad}
        <span
          class="size-1.5 rounded-full {LOAD_FILL[markLoad]}"
          aria-hidden="true"
        ></span>
        Estado actual
      {:else if mark === "done"}
        <Check class="size-3" aria-hidden="true" /> Hecho
      {:else}
        Siguiente
      {/if}
    </span>
  {/if}
  {#if Icon}
    <Icon class="size-5" aria-hidden="true" />
  {/if}
  <span
    lang={labelLang}
    class={layout === "step" ? "font-display font-semibold" : undefined}
    >{label}</span
  >
  {#if caveat}
    <span class="text-2xs font-medium text-warn">{caveat}</span>
  {:else if hint}
    <span class="text-xs text-ink-mute">{hint}</span>
  {/if}
  {#if badge}
    <!-- Allowed to wrap: the Badge is nowrap + overflow-hidden, and «Sin probar
         en real» is wider than a gesture tile on a 360 px phone, so it was
         clipped mid-word inside the tile's own overflow-hidden. -->
    <Badge
      variant="outline"
      class="h-auto max-w-full text-center text-2xs leading-tight whitespace-normal text-ink-mute"
      >{BADGE_TEXT[badge]}</Badge
    >
  {/if}
  {#if holdToConfirm && !busy}
    <span class={["text-xs", holding ? "text-ink-dim" : "text-ink-mute"]}>
      {holding ? "Mantené…" : "Mantené presionado para confirmar"}
    </span>
  {/if}
  {#if busy}
    <span class="text-xs text-ink-mute">Enviando…</span>
  {:else if stamped}
    <OutcomeLine {stamped} {now} />
  {/if}
</button>
