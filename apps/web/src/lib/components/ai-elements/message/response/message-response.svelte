<script lang="ts">
  // Thin alias so `Message.Response` and the standalone `Response` render
  // identically — see `../../response/response.svelte` for why this is not
  // Streamdown.
  import Response from "../../response/response.svelte";

  let { content = "", class: className }: { content?: string; class?: string } =
    $props();
</script>

<Response {content} class={className} />
