<script lang="ts">
  import { Card, CardContent } from '$lib/components/ui/card';

  let { title = 'Regions', value = '0' } = $props();
</script>

<Card class="bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="p-6">
    <div class="space-y-2">
      <p class="text-sm text-neutral-400">{title}</p>
      <p class="text-2xl font-bold text-white">{value}</p>
    </div>
  </CardContent>
</Card>
