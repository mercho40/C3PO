<script lang="ts">
  import { resolve } from "$app/paths";
  import AuthShell from "$lib/components/auth-shell.svelte";
  import Callout from "$lib/components/callout.svelte";
  import LoginForm from "$lib/components/login-form.svelte";
  import { titleFor } from "$lib/nav";
  import { estop } from "$lib/robot/estop.svelte";

  // No client-side "already signed in" redirect here any more: the page load
  // owns it. The old `$effect` fired on a STALE `data.user` after logout (which
  // navigated without invalidateAll) and caused a /login → /dashboard → /login
  // bounce; logout now invalidates, so the effect had nothing left to do.
  let { data } = $props();

  // "…para volver a Control VR", when the destination is a console page.
  const destination = $derived(
    titleFor(new URL(data.redirectTo, "http://x").pathname),
  );
  // Keep the destination across the switch to /signup.
  const carry = $derived(
    data.redirectTo === "/dashboard"
      ? ""
      : `?${new URLSearchParams({ redirectTo: data.redirectTo })}`,
  );
</script>

<svelte:head><title>Ingresar · C3PO</title></svelte:head>

<AuthShell
  title="Consola de operador"
  subtitle="Ingresá para controlar el robot."
>
  {#snippet notices()}
    <!-- The e-stop store is module-level and survives the client-side
         navigation here, so a stop that bounced off an expired session is
         still known — and the operator must not lose that on the way in. -->
    {#if estop.phase === "expired"}
      <Callout tone="danger" title="Tu última orden de PARAR no salió">
        Se envió con la sesión vencida, así que no llegó al robot. Si sigue en
        movimiento, usá la parada física.
      </Callout>
    {/if}
    {#if data.backendDown}
      <Callout tone="danger" title="No se pudo contactar al servidor">
        La consola no llega a la API, así que no se puede ingresar ahora. Si ya
        tenías una sesión, no se cerró: volvé a intentar cuando el servidor
        responda.
      </Callout>
    {/if}
    {#if data.expired}
      <Callout tone="info" title="Tu sesión venció">
        Ingresá de nuevo{destination ? ` para volver a ${destination}` : ""}.
      </Callout>
    {/if}
  {/snippet}

  <LoginForm
    redirectTo={data.redirectTo}
    providers={data.providers}
    initialError={data.oauthError}
  />

  {#snippet footer()}
    ¿No tenés cuenta?
    <a
      href="{resolve('/signup')}{carry}"
      class="text-ink underline-offset-4 hover:underline focus-visible:underline focus-visible:outline-none"
    >
      Crear cuenta
    </a>
  {/snippet}
</AuthShell>
