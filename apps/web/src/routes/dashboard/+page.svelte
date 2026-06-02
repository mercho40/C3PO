<script lang="ts">
  import Sidebar from '$lib/components/sidebar.svelte';
  import DashboardTopbar from '$lib/components/dashboard-topbar.svelte';
  import StatusCard from '$lib/components/status-card.svelte';
  import CircularProgress from '$lib/components/circular-progress.svelte';
  import MapCard from '$lib/components/map-card.svelte';
</script>

<div class="flex h-screen flex-col bg-neutral-800">
  <!-- Main Content -->
  <div class="flex flex-1 overflow-hidden">
    <!-- Sidebar -->
    <Sidebar />

    <!-- Content with Top Bar and Main Area -->
    <div class="flex flex-1 flex-col overflow-hidden">
      <!-- Top Bar -->
      <DashboardTopbar />

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
</div>

<style>
  :global(body) {
    margin: 0;
    padding: 0;
    overflow: hidden;
  }
</style>
