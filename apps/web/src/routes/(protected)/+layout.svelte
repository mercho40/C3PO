<script lang="ts">
  import { onMount, untrack } from "svelte";
  import AppSidebar from "$lib/components/app-sidebar.svelte";
  import DashboardTopbar from "$lib/components/dashboard-topbar.svelte";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import { RobotLive } from "$lib/robot/live-state.svelte";
  import { setRobotLive } from "$lib/robot/context";

  let { children, data } = $props();

  // One poller for the whole console, seeded from the layout load so the first
  // paint already has real values. Pages read it via `getRobotLive()`; keeping
  // it here means the trail and odometer survive navigation between the
  // dashboard and the map.
  const live = untrack(() => new RobotLive(data.state, data.online));
  setRobotLive(live);
  onMount(() => {
    live.start();
    return () => live.stop();
  });
</script>

<Sidebar.Provider
  open={data.sidebarOpen}
  class="h-svh overflow-hidden font-display text-foreground"
>
  <AppSidebar />
  <!-- `min-w-0`: the inset is a flex sibling of the sidebar, and flex items
       refuse to shrink below their content by default. Without it, between
       768px (where the sidebar stops being a sheet) and roughly 1024px the
       dashboard's headline held the inset wider than the viewport and the right
       edge of every panel was clipped off-screen. -->
  <Sidebar.Inset class="relative min-w-0 overflow-hidden bg-background">
    <!-- Ambient depth: a cool wash from the top edge and a deeper one bottom-right,
         so large flat regions (map canvas, empty chat) don't read as dead space. -->
    <div
      class="pointer-events-none absolute inset-0 z-0"
      style="background:
        radial-gradient(60% 40% at 50% 0%, rgba(159,197,255,0.10), rgba(159,197,255,0) 70%),
        radial-gradient(40% 40% at 100% 100%, rgba(74,125,209,0.12), rgba(74,125,209,0) 70%);"
    ></div>

    <div
      class="relative z-10 flex min-h-0 min-w-0 flex-1 flex-col gap-4 px-4 pb-4 sm:px-6 sm:pb-6 lg:px-7 lg:pb-7"
    >
      <DashboardTopbar />
      <div class="min-h-0 min-w-0 flex-1 overflow-y-auto">
        {@render children()}
      </div>
    </div>
  </Sidebar.Inset>
</Sidebar.Provider>
