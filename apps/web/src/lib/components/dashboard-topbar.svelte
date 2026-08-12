<script lang="ts">
  import { page } from "$app/state";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import RobotStatus from "./robot-status.svelte";
  import StopButton from "./stop-button.svelte";

  const titles: Record<string, string> = {
    "/dashboard": "Inicio",
    "/live-map": "Mapa en vivo",
    "/live-camera": "Cámaras",
    "/chat": "Chat",
  };
  const title = $derived(titles[page.url.pathname] ?? "");
</script>

<header class="flex h-14 shrink-0 items-center gap-3">
  <Sidebar.Trigger
    class="-ms-1 size-8 shrink-0 text-ink-mute hover:bg-accent hover:text-ink"
  />
  <!-- Hidden on phones. The row has to carry the connection state and a 44px
       stop, and the status chip grows again when there are faults; something had
       to give, and the label for the page the operator just tapped into is the
       least load-bearing thing on it. Squeezing it produced a truncated "I…". -->
  <h1 class="hidden truncate stamp text-lg text-ink sm:block sm:text-xl">
    {title}
  </h1>

  <!-- `shrink-0` so the stop can never be the thing that compresses; the title
       has `truncate` and gives up its width first. -->
  <div class="ms-auto flex shrink-0 items-center gap-2.5">
    <RobotStatus />
    <StopButton />
  </div>
</header>
