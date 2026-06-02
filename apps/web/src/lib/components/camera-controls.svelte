<script lang="ts">
  import { Card, CardContent } from '$lib/components/ui/card';

  let { title = 'Camera Controls' } = $props();

</script>

<Card class="bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="p-8">
    <div class="space-y-4">
      <h3 class="text-lg font-semibold text-white">{title}</h3>
      
      <!-- Controls Grid -->
      <div class="grid grid-cols-3 gap-4">
        <button class="rounded-lg bg-neutral-600 px-4 py-2 text-sm font-medium transition-colors hover:bg-neutral-500">
          🔴 Record
        </button>
        <button class="rounded-lg bg-neutral-600 px-4 py-2 text-sm font-medium transition-colors hover:bg-neutral-500">
          📸 Snapshot
        </button>
        <button class="rounded-lg bg-neutral-600 px-4 py-2 text-sm font-medium transition-colors hover:bg-neutral-500">
          ⚙️ Settings
        </button>
      </div>

      <!-- Status Info -->
      <div class="space-y-2 border-t border-neutral-600 pt-4">
        <div class="flex justify-between text-sm text-neutral-300">
          <span>Status:</span>
          <span class="text-green-400">● Connected</span>
        </div>
        <div class="flex justify-between text-sm text-neutral-300">
          <span>Resolution:</span>
          <span>1920x1080</span>
        </div>
        <div class="flex justify-between text-sm text-neutral-300">
          <span>FPS:</span>
          <span>30</span>
        </div>
      </div>
    </div>
  </CardContent>
</Card>
