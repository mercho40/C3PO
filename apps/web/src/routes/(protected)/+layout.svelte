<script lang="ts">
  import Sidebar from "$lib/components/sidebar.svelte";
  import DashboardTopbar from "$lib/components/dashboard-topbar.svelte";

  let { children } = $props();
</script>

<div class="font-display dark relative flex h-screen overflow-hidden bg-[#05070d] text-[#eaf1ff]">
  <!-- Radial glows -->
  <div
    class="pointer-events-none absolute inset-0 z-0"
    style="background:
      radial-gradient(60% 40% at 50% 0%, rgba(159,197,255,0.10), rgba(159,197,255,0) 70%),
      radial-gradient(40% 40% at 100% 100%, rgba(74,125,209,0.12), rgba(74,125,209,0) 70%);"
  ></div>

  <div class="relative z-10 shrink-0">
    <Sidebar />
  </div>

  <div class="relative z-10 flex min-w-0 flex-1 flex-col px-7 pb-7">
    <DashboardTopbar />
    <main class="min-h-0 flex-1 overflow-y-auto">
      {@render children()}
    </main>
  </div>
</div>
