<script lang="ts">
  import { resolve } from "$app/paths";
  import AuthShell from "#lib/components/auth-shell.svelte";
  import Callout from "#lib/components/callout.svelte";
  import SignupForm from "#lib/components/signup-form.svelte";

  // The "already signed in" redirect is the page load's alone (see /login).
  let { data } = $props();

  // Keep the destination across the switch to /login.
  const carry = $derived(
    data.redirectTo === "/dashboard"
      ? ""
      : `?${new URLSearchParams({ redirectTo: data.redirectTo })}`,
  );
</script>

<svelte:head><title>Crear cuenta · C3PO</title></svelte:head>

<AuthShell
  title="Crear cuenta"
  subtitle="Necesitás una cuenta para operar el robot."
>
  {#snippet notices()}
    {#if data.backendDown}
      <Callout tone="danger" title="No se pudo contactar al servidor">
        La consola no llega a la API, así que no se puede crear la cuenta ahora.
        Probá de nuevo cuando el servidor responda.
      </Callout>
    {/if}
  {/snippet}

  <SignupForm
    redirectTo={data.redirectTo}
    providers={data.providers}
    initialError={data.oauthError}
  />

  {#snippet footer()}
    ¿Ya tenés cuenta?
    <a
      href="{resolve('login')}{carry}"
      class="text-ink underline-offset-4 hover:underline focus-visible:underline focus-visible:outline-none"
    >
      Ingresar
    </a>
  {/snippet}
</AuthShell>
