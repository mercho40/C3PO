<script lang="ts">
  import { Loader2 } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Separator } from "$lib/components/ui/separator/index.js";
  import Callout from "./callout.svelte";
  import SocialAuthButtons, {
    type SocialProvider,
  } from "./social-auth-buttons.svelte";
  import {
    AUTH_NETWORK_ERROR,
    authClient,
    authErrorField,
    authErrorMessage,
    type AuthErrorField,
  } from "$lib/auth-client";

  let {
    redirectTo,
    providers,
    initialError = null,
  }: {
    /** Sanitised by the page load — a same-origin path. */
    redirectTo: string;
    /** OAuth providers back has credentials for. */
    providers: SocialProvider[];
    /** e.g. an OAuth failure Better Auth bounced back here. */
    initialError?: string | null;
  } = $props();

  const id = $props.id();

  let email = $state("");
  let password = $state("");
  let loading = $state(false);
  let socialPending = $state<SocialProvider | null>(null);
  // Writable derived: starts as the page's error, replaced by the form's own.
  let error = $derived(initialError ?? "");
  let errorField = $state<AuthErrorField>(null);

  const busy = $derived(loading || socialPending !== null);
  const emailInvalid = $derived(
    errorField === "email" || errorField === "credentials",
  );
  const passwordInvalid = $derived(
    errorField === "password" || errorField === "credentials",
  );

  async function handleSubmit(e: Event) {
    e.preventDefault();
    if (busy) return;
    loading = true;
    error = "";
    errorField = null;
    try {
      const { error: authError } = await authClient.signIn.email({
        email,
        password,
      });
      if (authError) {
        error = authErrorMessage(authError);
        errorField = authErrorField(authError.code);
        loading = false;
        return;
      }
      // Full reload rather than goto(): the session cookie has just been set,
      // and hooks.server.ts reads it during SSR. `loading` stays true — the page
      // is leaving, and re-enabling the button invited a second submit.
      window.location.href = redirectTo;
    } catch {
      // A refused connection THROWS out of better-fetch (see AUTH_NETWORK_ERROR);
      // without this the form spun "Ingresando…" forever.
      error = AUTH_NETWORK_ERROR;
      loading = false;
    }
  }

  // The success paths leave `loading` set on purpose (the page is leaving), so
  // a back/forward-cache restore — Back from the dashboard, or from the OAuth
  // provider — brought the form back frozen on its spinner with nothing in
  // flight. Unlock it on that restore only (`persisted`).
  function onpageshow(e: PageTransitionEvent) {
    if (!e.persisted) return;
    loading = false;
    socialPending = null;
  }
</script>

<svelte:window {onpageshow} />

<form onsubmit={handleSubmit} class="flex flex-col gap-4">
  <div class="flex flex-col gap-2">
    <Label for="email-{id}" class="text-xs text-ink-dim">Email</Label>
    <Input
      id="email-{id}"
      type="email"
      autocomplete="email"
      placeholder="operador@ejemplo.com"
      required
      disabled={busy}
      bind:value={email}
      aria-invalid={emailInvalid || undefined}
      aria-describedby={emailInvalid ? `error-${id}` : undefined}
      class="h-10 border-hairline-strong bg-wash text-base text-ink placeholder:text-ink-mute md:text-sm"
    />
  </div>

  <div class="flex flex-col gap-2">
    <Label for="password-{id}" class="text-xs text-ink-dim">Contraseña</Label>
    <Input
      id="password-{id}"
      type="password"
      autocomplete="current-password"
      required
      disabled={busy}
      bind:value={password}
      aria-invalid={passwordInvalid || undefined}
      aria-describedby={passwordInvalid ? `error-${id}` : undefined}
      class="h-10 border-hairline-strong bg-wash text-base text-ink md:text-sm"
    />
  </div>

  {#if error}
    <div id="error-{id}">
      <Callout tone="danger" class="text-xs">{error}</Callout>
    </div>
  {/if}

  <Button
    type="submit"
    disabled={busy}
    class="h-10 w-full gap-2 cta text-sm font-medium"
  >
    {#if loading}
      <Loader2 class="size-4 motion-safe:animate-spin" aria-hidden="true" />
      Ingresando…
    {:else}
      Ingresar
    {/if}
  </Button>

  {#if providers.length > 0}
    <div class="flex items-center gap-3 py-1" aria-hidden="true">
      <Separator class="flex-1 bg-hairline" />
      <span class="eyebrow">o</span>
      <Separator class="flex-1 bg-hairline" />
    </div>

    <SocialAuthButtons
      {providers}
      {redirectTo}
      disabled={loading}
      bind:pending={socialPending}
      onerror={(message) => (error = message)}
    />
  {/if}
</form>
