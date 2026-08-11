<script lang="ts">
  import AppSidebar from "$lib/components/app-sidebar.svelte";
  import DashboardTopbar from "$lib/components/dashboard-topbar.svelte";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";

  let { children, data } = $props();
</script>

<Sidebar.Provider open={data.sidebarOpen} class="font-display h-svh overflow-hidden text-[#eaf1ff]">
  <AppSidebar />
  <Sidebar.Inset class="relative overflow-hidden bg-[#06090f]">
    <!-- Radial glows -->
    <div
      class="pointer-events-none absolute inset-0 z-0"
      style="background:
        radial-gradient(60% 40% at 50% 0%, rgba(159,197,255,0.10), rgba(159,197,255,0) 70%),
        radial-gradient(40% 40% at 100% 100%, rgba(74,125,209,0.12), rgba(74,125,209,0) 70%);"
    ></div>

    <div class="relative z-10 flex min-h-0 flex-1 flex-col px-7 pb-7">
      <DashboardTopbar />
      <div class="min-h-0 flex-1 overflow-y-auto">
        {@render children()}
      </div>
    </div>
  </Sidebar.Inset>
</Sidebar.Provider>
