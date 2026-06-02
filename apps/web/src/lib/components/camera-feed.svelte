<script lang="ts">
  import { Card, CardContent } from '$lib/components/ui/card';

  let { title = 'Camera Feed' } = $props();

</script>

<Card class="flex flex-col bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="flex flex-1 items-center justify-center p-8">
    <div class="relative aspect-video w-full overflow-hidden rounded-lg bg-neutral-600">
      <!-- Video feed placeholder with corner markers -->
      <div class="flex h-full w-full items-center justify-center">
        <!-- Corner markers -->
        <div class="absolute top-4 left-4 h-6 w-6 border-t-2 border-l-2 border-white" />
        <div class="absolute top-4 right-4 h-6 w-6 border-t-2 border-r-2 border-white" />
        <div class="absolute bottom-4 left-4 h-6 w-6 border-b-2 border-l-2 border-white" />
        <div class="absolute bottom-4 right-4 h-6 w-6 border-b-2 border-r-2 border-white" />
        
        <!-- Center text -->
        <span class="text-sm text-neutral-400">📹 Camera Feed</span>
      </div>
    </div>
  </CardContent>
</Card>
