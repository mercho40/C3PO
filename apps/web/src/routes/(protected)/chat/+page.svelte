<script lang="ts">
  import Sidebar from "$lib/components/sidebar.svelte";
  import DashboardTopbar from "$lib/components/dashboard-topbar.svelte";
  import ChatBox from "$lib/components/chat-box.svelte";
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
      <main class="flex-1 overflow-hidden bg-neutral-800 p-8">
        <div class="h-full">
          <ChatBox />
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
