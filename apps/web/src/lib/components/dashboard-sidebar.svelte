<script lang="ts">
  import {
    LayoutDashboard,
    Radio,
    Map,
    MessageSquare,
    Zap,
    MoreVertical,
  } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button";

  const menuItems = [
    { label: "Dashboard", icon: LayoutDashboard },
    { label: "Live Camera", icon: Radio },
    { label: "Live Map", icon: Map },
    { label: "Chat", icon: MessageSquare },
    { label: "Voice control", icon: Zap },
    { label: "Connection guide", icon: MoreVertical },
  ];

  let activeItem = $state("Dashboard");
</script>

<aside class="flex h-full w-32 flex-col gap-4 bg-neutral-700 p-4">
  <!-- Logo/Home - Centered -->
  <div class="flex justify-center">
    <Button
      variant="ghost"
      size="icon"
      aria-label="Home"
      class="h-10 w-10 hover:bg-neutral-600"
    >
      <img src="/logo.svg" alt="C3PO" class="h-7 w-7 object-contain" />
    </Button>
  </div>

  <!-- Menu Items -->
  <nav class="flex flex-col gap-2">
    {#each menuItems as item (item.label)}
      {@const Icon = item.icon}
      <Button
        variant="ghost"
        onclick={() => (activeItem = item.label)}
        class="h-auto flex-col gap-2 px-3 py-2 text-xs font-medium {activeItem ===
        item.label
          ? 'bg-neutral-600 text-white'
          : 'text-neutral-400 hover:bg-neutral-600 hover:text-white'}"
      >
        <Icon class="h-4 w-4" />
        <span class="text-center text-xs">{item.label.split(" ")[0]}</span>
      </Button>
    {/each}
  </nav>

  <!-- More Button -->
  <Button
    variant="ghost"
    class="mt-auto h-auto flex-col gap-2 px-3 py-2 text-xs font-medium text-neutral-400 hover:bg-neutral-600 hover:text-white"
  >
    <MoreVertical class="h-4 w-4" />
    <span class="text-xs">More</span>
  </Button>
</aside>
