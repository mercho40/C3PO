<script lang="ts">
  import DashboardSidebar from '$lib/components/dashboard-sidebar.svelte';
  import DashboardTopbar from '$lib/components/dashboard-topbar.svelte';
  import StatusCard from '$lib/components/status-card.svelte';
  import CircularProgress from '$lib/components/circular-progress.svelte';
  import MapCard from '$lib/components/map-card.svelte';

  let { data } = $props();
</script>

<div class="flex h-screen flex-col bg-neutral-800">
  <!-- Top Bar -->
  <DashboardTopbar />

  <!-- Main Content -->
  <div class="flex flex-1 overflow-hidden">
    <!-- Sidebar -->
    <DashboardSidebar />

    <!-- Content Area -->
    <main class="flex-1 overflow-y-auto bg-neutral-800 p-8">
      <div class="space-y-6">
        <!-- Grid Layout for cards -->
        <div class="grid gap-6">
          <!-- Row 1: Status + Network Latency + Progress -->
          <div class="grid grid-cols-3 gap-6">
            <!-- Status Card (spans 1) -->
            <StatusCard title="Status*" />

            <!-- Network Latency Card (spans 1) -->
            <StatusCard title="Network Latency" />

            <!-- Circular Progress (spans 1) -->
            <CircularProgress percentage={87} />
          </div>

          <!-- Row 2: Map (full width) -->
          <div class="grid gap-6">
            <MapCard title="World Map" />
          </div>
        </div>
      </div>
    </main>
  </div>
</div>

<style>
  :global(body) {
    margin: 0;
    padding: 0;
    overflow: hidden;
  }
</style>
