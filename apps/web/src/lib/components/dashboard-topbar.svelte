<script lang="ts">
  import { page } from "$app/state";
  import * as Sidebar from "#lib/components/ui/sidebar/index.js";
  import { titleFor } from "#lib/nav.js";
  import RobotStatus from "./robot-status.svelte";
  import StopButton from "./stop-button.svelte";
  import VoiceChip from "./shell/voice-chip.svelte";
  import GateChip from "./shell/gate-chip.svelte";

  // From `#lib/nav`, the one list of console pages — this used to be a private
  // map of four routes, so /vr-control got an empty <h1>. An error page is
  // titled as the error, not as the page that failed to load.
  const title = $derived(
    page.error
      ? page.status === 404
        ? "Página no encontrada"
        : "Error"
      : titleFor(page.url.pathname),
  );
</script>

<!-- The topbar owns the browser-tab title for every console page; pages do not
     set their own, so the tab and the heading cannot disagree. -->
<svelte:head>
  <title>{title ? `${title} · C3PO` : "C3PO"}</title>
</svelte:head>

<!-- Two layouts, one markup.

     Below `xl` the topbar is two rows: [menu · title · PARAR] and, under it, the
     status chips on a row of their own that wraps rather than clips. A single
     row cannot hold them: at 768px the inset is ~460px next to the open
     sidebar, so the title became "IN…", the link word "EN LÍNE…", and with Nav
     armed the gate chip crushed the status to 33px — dot, link word, battery
     and faults all gone, in the states that matter most. The stop and the link
     state must always be readable; a 32px taller header is the cheaper loss.
     The page title is visible again on phones too (it was sr-only there).

     From `xl` it is one row, the chips in a `fit-content` column capped at
     55%: past that they wrap onto a second line inside their own column
     instead of eating the title to one letter. (`xl`, not `lg`: at 1024px the
     open sidebar leaves ~710px, and with Nav armed and voice on the chips
     stacked three deep beside a truncated title.)

     Either way the stop's column is `auto` and never gives way — the old flex
     row once pushed a "Sin confirmar" stop 11px off a 360px screen.

     No `@container` here, deliberately: size containment makes the header a
     stacking context, and the stop must rise above modal overlays (below). -->
<header
  class="grid shrink-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-1.5 pt-1.5 sm:gap-x-3 xl:min-h-14 xl:grid-cols-[auto_minmax(0,1fr)_fit-content(55%)_auto] xl:py-1.5"
>
  <Sidebar.Trigger
    class="col-start-1 row-start-1 -ms-1 size-8 shrink-0 text-ink-mute hover:bg-accent hover:text-ink"
    title="Mostrar u ocultar el menú (Ctrl+B)"
  />

  <div class="col-start-2 row-start-1 min-w-0">
    {#if title}
      <h1 class="truncate stamp text-lg text-ink sm:text-xl">{title}</h1>
    {/if}
  </div>

  <!-- Row two below `xl`, spanning under the menu and the stop; the third
       column from `xl`. Status first: it is the one that is always there. -->
  <div
    class="col-span-full row-start-2 flex min-w-0 flex-wrap items-center gap-2 xl:col-span-1 xl:col-start-3 xl:row-start-1 xl:justify-end"
  >
    <RobotStatus />
    <GateChip />
    <VoiceChip />
  </div>

  <!-- Above every modal overlay. bits-ui overlays sit at z-50 and set
       `pointer-events: none` on <body> while open, so the logout dialog, the
       chat-history sheet and — on a phone — the nav sheet all used to cover the
       stop: the first tap on PARAR only dismissed the overlay. The stop is
       idempotent, so letting it bypass a modal is safe; nothing else here is.
       (Only works because no ancestor of this element creates a stacking
       context — see the protected layout. The phone nav sheet leaves this
       corner of its header empty for it — ui/sidebar/sidebar.svelte.) -->
  <div
    class="pointer-events-auto relative z-60 col-start-3 row-start-1 flex items-center justify-end xl:col-start-4"
  >
    <StopButton />
  </div>
</header>
