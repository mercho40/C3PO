<script lang="ts">
  import { Card, CardContent } from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";

  let { title = "World Map" } = $props();
</script>

<Card class="col-span-2 bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="flex items-center justify-center p-8">
    <div class="w-full space-y-4">
      <!-- Map placeholder -->
      <div
        class="relative aspect-video w-full overflow-hidden rounded-lg bg-neutral-600"
      >
        <!-- Simple world map SVG -->
        <svg
          viewBox="0 0 960 500"
          class="h-full w-full opacity-40"
          role="img"
          aria-label={title}
        >
          <!-- Simplified world map outline -->
          <g fill="none" stroke="currentColor" stroke-width="0.5">
            <!-- Simple rectangle representing continents -->
            <rect
              x="100"
              y="80"
              width="80"
              height="60"
              fill="currentColor"
              opacity="0.1"
            />
            <rect
              x="250"
              y="100"
              width="100"
              height="100"
              fill="currentColor"
              opacity="0.1"
            />
            <rect
              x="500"
              y="50"
              width="120"
              height="80"
              fill="currentColor"
              opacity="0.1"
            />
            <rect
              x="700"
              y="100"
              width="100"
              height="90"
              fill="currentColor"
              opacity="0.1"
            />
          </g>
        </svg>
      </div>

      <!-- Search bar -->
      <Input
        placeholder="Search locations..."
        class="border-transparent bg-neutral-600 text-white placeholder:text-neutral-400"
      />
    </div>
  </CardContent>
</Card>
