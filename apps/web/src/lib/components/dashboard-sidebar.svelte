<script lang="ts">
  import { LayoutDashboard, Radio, Map, MessageSquare, Zap, MoreVertical } from '@lucide/svelte';

  const menuItems = [
    { label: 'Dashboard', icon: LayoutDashboard },
    { label: 'Live Camera', icon: Radio },
    { label: 'Live Map', icon: Map },
    { label: 'Chat', icon: MessageSquare },
    { label: 'Voice control', icon: Zap },
    { label: 'Connection guide', icon: MoreVertical },
  ];

  let activeItem = 'Dashboard';
</script>

<aside class="flex h-full w-32 flex-col gap-4 bg-neutral-700 p-4">
  <!-- Logo/Home - Centered -->
  <div class="flex justify-center">
    <button class="flex h-10 w-10 items-center justify-center rounded-full bg-neutral-600 hover:bg-neutral-500">
      <div class="h-6 w-6 rounded-full bg-neutral-400" />
    </button>
  </div>

  <!-- Menu Items -->
  <nav class="flex flex-col gap-2">
    {#each menuItems as item (item.label)}
      <button
        on:click={() => (activeItem = item.label)}
        class="flex flex-col items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition-colors {activeItem === item.label
          ? 'bg-neutral-600 text-white'
          : 'text-neutral-400 hover:bg-neutral-600 hover:text-white'}"
      >
        <svelte:component this={item.icon} class="h-4 w-4" />
        <span class="text-center text-xs">{item.label.split(' ')[0]}</span>
      </button>
    {/each}
  </nav>

  <!-- More Button -->
  <button class="mt-auto flex flex-col items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium text-neutral-400 transition-colors hover:bg-neutral-600 hover:text-white">
    <MoreVertical class="h-4 w-4" />
    <span class="text-xs">More</span>
  </button>
</aside>
