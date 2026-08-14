<script lang="ts">
  import { goto } from "$app/navigation";
  import AuthShell from "$lib/components/auth-shell.svelte";
  import SignupForm from "$lib/components/signup-form.svelte";

  let { data } = $props();
  $effect(() => {
    if (data.user) goto("/dashboard", { invalidateAll: true });
  });
</script>

<svelte:head><title>Crear cuenta · C3PO</title></svelte:head>

<AuthShell
  title="Crear cuenta"
  subtitle="Necesitás una cuenta para operar el robot."
>
  <SignupForm />
  {#snippet footer()}
    ¿Ya tenés cuenta?
    <a href="/login" class="text-ink underline-offset-4 hover:underline">
      Ingresar
    </a>
  {/snippet}
</AuthShell>
