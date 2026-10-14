<!--
  Hold-to-move. Pressed = moving, released = stopping.

  Pointer-only used to be the whole story: keyboard focus plus Space fired a
  click nothing handled, and the active state was a `data-active` attribute a
  screen reader never heard. Space/Enter now hold it like a press (key repeat is
  ignored, so holding the key is one press, not thirty), and `aria-pressed`
  says whether it is held.

  Release is belt-and-braces on purpose: pointerup, pointerleave,
  pointercancel, keyup and blur all end the hold here, and the controller also
  releases on window pointerup/blur and on page hide — a held button the page
  forgets about is a robot walking with nobody holding anything.

  Only the pointer that started the hold can end it, and only while it is this
  button's hold: two Quest controllers are two pointers, and an unguarded
  pointerleave from the second ray grazing "Atrás" called `stopWalking()` —
  which does not ask which direction it stops — and ended the walk the first
  ray was holding on "Adelante". Only the primary button starts one (a
  right-click or a stylus barrel button is not a press), and the long-press
  context menu is suppressed, because on a touch screen it opens mid-hold.

  `aria-disabled` plus a guard rather than `disabled`: it can flip while the
  operator is on the button (PARAR in progress), and a native disabled button
  drops focus. Chrome also dispatches pointer events to disabled controls now,
  so `disabled` alone was never the guard it looked like.

  Cyan when active is correct here and nowhere else on these tiles: an active
  hold means the robot is being driven under power.
-->
<script lang="ts">
  import type { Component } from "svelte";
  import { cn } from "#lib/utils.js";

  let {
    label,
    icon: Icon,
    active,
    disabled = false,
    describedby,
    onstart,
    onstop,
  }: {
    label: string;
    icon: Component;
    active: boolean;
    disabled?: boolean;
    describedby?: string;
    onstart: () => void;
    onstop: () => void;
  } = $props();

  const HOLD_KEYS = new Set([" ", "Enter"]);
  // A modified key is never a hold: Shift+Space is the global e-stop, and the
  // layout swallows it in the capture phase — this is the second guard.
  const plain = (e: KeyboardEvent) =>
    !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey;

  // The pointer that started the current hold, so another one cannot end it.
  let holdPointer: number | null = null;

  function onpointerdown(e: PointerEvent) {
    if (disabled || e.button !== 0) return;
    holdPointer = e.pointerId;
    onstart();
  }

  function onpointerend(e: PointerEvent) {
    if (holdPointer !== null && e.pointerId !== holdPointer) return;
    holdPointer = null;
    if (active) onstop();
  }

  function onkeydown(e: KeyboardEvent) {
    if (!HOLD_KEYS.has(e.key) || !plain(e)) return;
    // Space would scroll the page; Enter would fire a click. Neither is wanted.
    e.preventDefault();
    if (e.repeat || disabled) return;
    onstart();
  }

  function onkeyup(e: KeyboardEvent) {
    if (!HOLD_KEYS.has(e.key)) return;
    e.preventDefault();
    if (active) onstop();
  }

  // Only THIS button's hold. Pressing "Adelante" moves focus there, which blurs
  // "Atrás" a moment after the press began — and `stopWalking()` does not ask
  // which direction it is stopping, so an unguarded blur ended the walk that
  // had just started.
  function onblur() {
    holdPointer = null;
    if (active) onstop();
  }
</script>

<button
  type="button"
  aria-pressed={active}
  aria-describedby={describedby}
  aria-disabled={disabled}
  {onpointerdown}
  onpointerup={onpointerend}
  onpointerleave={onpointerend}
  onpointercancel={onpointerend}
  oncontextmenu={(e) => e.preventDefault()}
  {onkeydown}
  {onkeyup}
  {onblur}
  class={cn(
    "flex min-h-28 touch-none flex-col items-center justify-center gap-2 rounded-lg border border-hairline bg-wash font-display text-lg font-semibold text-ink transition-colors duration-100 select-none focus-visible:border-accent-edge focus-visible:ring-3 focus-visible:ring-cyan-hot/50 focus-visible:outline-none aria-disabled:cursor-not-allowed aria-disabled:opacity-50",
    active && "border-cyan bg-cyan/15 text-cyan",
  )}
>
  <Icon class="size-7" aria-hidden="true" />
  {label}
</button>
