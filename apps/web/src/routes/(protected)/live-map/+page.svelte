<script lang="ts">
  import Sidebar from "$lib/components/sidebar.svelte";
  import DashboardTopbar from "$lib/components/dashboard-topbar.svelte";
  import WorldMap from "$lib/components/world-map.svelte";
  import MapStatCard from "$lib/components/map-stat-card.svelte";
  import { Input } from "$lib/components/ui/input";
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
          <!-- Grid Layout: Map on left, stats on right -->
          <div class="grid grid-cols-3 gap-6">
            <!-- Map (takes 2 columns) -->
            <div class="col-span-2">
              <WorldMap />
            </div>

            <!-- Right Sidebar with search and stats -->
            <div class="flex flex-col gap-4">
              <!-- Search input -->
              <Input
                placeholder="Search location..."
                class="border-transparent bg-neutral-700 text-white placeholder:text-neutral-400"
              />

              <!-- Stats Grid 2x2 -->
              <div class="grid grid-cols-2 gap-4">
                <MapStatCard title="Active" value="127" />
                <MapStatCard title="Regions" value="42" />
                <MapStatCard title="Latency" value="24ms" />
                <MapStatCard title="Uptime" value="99.8%" />
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
