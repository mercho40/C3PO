<script lang="ts">
  import { Card, CardContent } from "$lib/components/ui/card";
  import { Video } from "@lucide/svelte";

  let { title = "Camera Feed" } = $props();
</script>

<Card class="flex flex-col bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="flex flex-1 items-center justify-center p-8">
    <div
      class="relative aspect-video w-full overflow-hidden rounded-lg bg-neutral-600"
    >
      <!-- Video feed placeholder with corner markers -->
      <div class="flex h-full w-full items-center justify-center">
        <!-- Corner markers -->
        <div
          class="absolute top-4 left-4 h-6 w-6 border-t-2 border-l-2 border-white"
        ></div>
        <div
          class="absolute top-4 right-4 h-6 w-6 border-t-2 border-r-2 border-white"
        ></div>
        <div
          class="absolute bottom-4 left-4 h-6 w-6 border-b-2 border-l-2 border-white"
        ></div>
        <div
          class="absolute right-4 bottom-4 h-6 w-6 border-r-2 border-b-2 border-white"
        ></div>

        <!-- Center label -->
        <div class="flex items-center gap-2 text-sm text-neutral-400">
          <Video class="size-4" />
          <span>{title}</span>
        </div>
      </div>
    </div>
  </CardContent>
</Card>
