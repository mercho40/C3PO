<script lang="ts">
  import SignupForm from "$lib/components/signup-form.svelte";
  import { goto } from "$app/navigation";

  let { data } = $props();
  $effect(() => {
    if (data.user) goto("/", { invalidateAll: true });
  });
</script>

<div class="flex min-h-svh items-center justify-center p-6 md:p-10">
  <SignupForm />
</div>
