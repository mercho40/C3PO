<script lang="ts">
  import { Card, CardContent } from '$lib/components/ui/card';

  let { percentage = 87, radius = 45 } = $props();

  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percentage / 100) * circumference;
</script>

<Card class="flex items-center justify-center bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="flex items-center justify-center p-8">
    <div class="relative h-48 w-48">
      <svg class="h-full w-full" viewBox="0 0 120 120">
        <!-- Background circle -->
        <circle
          cx="60"
          cy="60"
          r={radius}
          fill="none"
          stroke="currentColor"
          stroke-width="8"
          class="text-neutral-600"
        />
        <!-- Progress circle -->
        <circle
          cx="60"
          cy="60"
          r={radius}
          fill="none"
          stroke="white"
          stroke-width="8"
          stroke-dasharray={circumference}
          stroke-dashoffset={offset}
          stroke-linecap="round"
          style="transition: stroke-dashoffset 0.5s ease; transform-origin: 50% 50%; transform: rotate(-90deg)"
        />
      </svg>
      <!-- Center text -->
      <div class="absolute inset-0 flex items-center justify-center">
        <span class="text-4xl font-bold text-white">{percentage}%</span>
      </div>
    </div>
  </CardContent>
</Card>
