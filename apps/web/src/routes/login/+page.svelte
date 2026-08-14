<script lang="ts">
  import { goto } from "$app/navigation";
  import AuthShell from "$lib/components/auth-shell.svelte";
  import LoginForm from "$lib/components/login-form.svelte";

  let { data } = $props();
  $effect(() => {
    if (data.user) goto("/dashboard", { invalidateAll: true });
  });
</script>

<svelte:head><title>Ingresar · C3PO</title></svelte:head>

<AuthShell
  title="Consola de operador"
  subtitle="Ingresá para controlar el robot."
>
  <LoginForm />
  {#snippet footer()}
    ¿No tenés cuenta?
    <a href="/signup" class="text-ink underline-offset-4 hover:underline">
      Crear una
    </a>
  {/snippet}
</AuthShell>
