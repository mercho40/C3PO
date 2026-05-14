<script lang="ts">
  import LoginForm from "$lib/components/login-form.svelte";
  import { goto } from "$app/navigation";

  let { data } = $props();
  $effect(() => {
    if (data.user) goto("/", { invalidateAll: true });
  });
</script>

<div class="flex min-h-svh items-center justify-center p-6 md:p-10">
  <LoginForm />
</div>
