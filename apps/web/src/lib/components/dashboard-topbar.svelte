<script lang="ts">
  import { page } from "$app/state";
  import { Button } from "$lib/components/ui/button";
  import {
    Avatar,
    AvatarImage,
    AvatarFallback,
  } from "$lib/components/ui/avatar";

  const user = $derived(page.data.user);
  const displayName = $derived(user?.name ?? user?.email ?? "user");
  const initials = $derived(
    displayName
      .split(/\s+/)
      .map((part) => part[0] ?? "")
      .join("")
      .slice(0, 2)
      .toUpperCase(),
  );
</script>

<header class="flex items-center justify-between bg-neutral-800 px-8 py-4">
  <!-- Left side (empty for spacing) -->
  <div></div>

  <!-- Right side - User profile -->
  <Button
    variant="ghost"
    class="h-auto items-center gap-3 px-4 py-2 text-white hover:bg-neutral-700"
  >
    <span class="text-sm font-medium">@{displayName}</span>
    <Avatar class="h-8 w-8">
      {#if user?.image}
        <AvatarImage src={user.image} alt={displayName} />
      {/if}
      <AvatarFallback class="bg-neutral-600 text-neutral-300"
        >{initials}</AvatarFallback
      >
    </Avatar>
  </Button>
</header>
