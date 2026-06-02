<script lang="ts">
  import { Card, CardContent } from '$lib/components/ui/card';

  let { title = 'World Map' } = $props();
</script>

<Card class="col-span-2 flex flex-col bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="flex flex-1 items-center justify-center p-8">
    <div class="w-full space-y-4">
      <!-- Map placeholder with pixel grid effect -->
      <div class="relative w-full overflow-hidden rounded-lg bg-neutral-600 aspect-video">
        <svg viewBox="0 0 960 500" class="h-full w-full opacity-50">
          <!-- Simple world map grid -->
          <defs>
            <pattern id="grid" width="10" height="10" patternUnits="userSpaceOnUse">
              <path d="M 10 0 L 0 0 0 10" fill="none" stroke="currentColor" stroke-width="0.5" />
            </pattern>
          </defs>
          <rect width="960" height="500" fill="url(#grid)" />
          
          <!-- Simplified continents -->
          <g fill="currentColor" opacity="0.3">
            <rect x="100" y="80" width="80" height="60" />
            <rect x="250" y="100" width="100" height="100" />
            <rect x="500" y="50" width="120" height="80" />
            <rect x="700" y="100" width="100" height="90" />
          </g>
        </svg>
      </div>
    </div>
  </CardContent>
</Card>
