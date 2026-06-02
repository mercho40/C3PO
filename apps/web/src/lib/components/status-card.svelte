<script lang="ts">
  import { Card, CardContent, CardHeader, CardTitle } from '$lib/components/ui/card';

  let { title = 'Status', content = '' } = $props();

</script>

<Card class="bg-neutral-700 text-white ring-neutral-600">
  <CardHeader class="pb-3">
    <CardTitle class="text-lg font-semibold">{title}</CardTitle>
  </CardHeader>
  <CardContent class="text-neutral-300">
    {#if content}
      <p>{content}</p>
    {/if}
  </CardContent>
</Card>
