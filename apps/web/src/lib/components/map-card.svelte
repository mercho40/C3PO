<script lang="ts">
  import { Card, CardContent } from '$lib/components/ui/card';

  let { title = 'World Map' } = $props();
</script>

<Card class="col-span-2 bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="flex items-center justify-center p-8">
    <div class="w-full space-y-4">
      <!-- Map placeholder -->
      <div class="relative aspect-video w-full overflow-hidden rounded-lg bg-neutral-600">
        <!-- Simple world map SVG -->
        <svg viewBox="0 0 960 500" class="h-full w-full opacity-40">
          <!-- Simplified world map outline -->
          <g fill="none" stroke="currentColor" stroke-width="0.5">
            <!-- Simple rectangle representing continents -->
            <rect x="100" y="80" width="80" height="60" fill="currentColor" opacity="0.1" />
            <rect x="250" y="100" width="100" height="100" fill="currentColor" opacity="0.1" />
            <rect x="500" y="50" width="120" height="80" fill="currentColor" opacity="0.1" />
            <rect x="700" y="100" width="100" height="90" fill="currentColor" opacity="0.1" />
          </g>
        </svg>
      </div>

      <!-- Search bar -->
      <div class="w-full">
        <input
          type="text"
          placeholder="Search locations..."
          class="w-full rounded-lg bg-neutral-600 px-4 py-2 text-sm text-white placeholder-neutral-400 focus:outline-none focus:ring-2 focus:ring-neutral-500"
        />
      </div>
    </div>
  </CardContent>
</Card>
