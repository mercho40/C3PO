<script lang="ts">
  import Sidebar from '$lib/components/sidebar.svelte';
  import DashboardTopbar from '$lib/components/dashboard-topbar.svelte';
  import CameraFeed from '$lib/components/camera-feed.svelte';
  import CameraControls from '$lib/components/camera-controls.svelte';
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
          <h1 class="text-2xl font-bold text-white">Camera</h1>

          <!-- Grid Layout -->
          <div class="grid gap-6">
            <!-- Camera Feed (2/3 height) -->
            <div class="grid grid-cols-3 gap-6">
              <div class="col-span-2">
                <CameraFeed />
              </div>
              <!-- Controls on the right -->
              <CameraControls />
            </div>

            <!-- Stats below -->
            <div class="rounded-lg bg-neutral-700 p-6 text-white ring-1 ring-neutral-600">
              <h3 class="mb-4 text-lg font-semibold">Recording History</h3>
              <div class="space-y-2 text-sm text-neutral-300">
                <p>No recordings yet. Start recording to see history here.</p>
              </div>
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
