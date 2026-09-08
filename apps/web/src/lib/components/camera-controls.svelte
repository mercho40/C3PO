<script lang="ts">
  import { Card, CardContent } from "$lib/components/ui/card";
  import { Button } from "$lib/components/ui/button";
  import { Separator } from "$lib/components/ui/separator";
  import { Circle, Camera, Settings } from "@lucide/svelte";

  let { title = "Camera Controls" } = $props();
</script>

<Card class="bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="space-y-4 p-8">
    <h3 class="text-lg font-semibold text-white">{title}</h3>

    <!-- Controls Grid -->
    <div class="grid grid-cols-3 gap-4">
      <Button
        variant="secondary"
        class="bg-neutral-600 text-white hover:bg-neutral-500"
      >
        <Circle class="size-4 fill-red-500 text-red-500" />
        Record
      </Button>
      <Button
        variant="secondary"
        class="bg-neutral-600 text-white hover:bg-neutral-500"
      >
        <Camera class="size-4" />
        Snapshot
      </Button>
      <Button
        variant="secondary"
        class="bg-neutral-600 text-white hover:bg-neutral-500"
      >
        <Settings class="size-4" />
        Settings
      </Button>
    </div>

    <Separator class="bg-neutral-600" />

    <!-- Status Info -->
    <div class="space-y-2">
      <div class="flex justify-between text-sm text-neutral-300">
        <span>Status:</span>
        <span class="text-green-400">Connected</span>
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
  </CardContent>
</Card>
