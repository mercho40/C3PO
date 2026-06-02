<script lang="ts">
  import { User } from '@lucide/svelte';

  let username = 'username';
</script>

<header class="flex items-center justify-between bg-neutral-800 px-8 py-4">
  <!-- Left side (empty for spacing) -->
  <div />

  <!-- Right side - User profile -->
  <button class="flex items-center gap-3 rounded-lg px-4 py-2 transition-colors hover:bg-neutral-700">
    <span class="text-sm font-medium text-white">@{username}</span>
    <div class="flex h-8 w-8 items-center justify-center rounded-full bg-neutral-600">
      <User class="h-5 w-5 text-neutral-300" />
    </div>
  </button>
</header>
