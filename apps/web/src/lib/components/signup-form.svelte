<script lang="ts">
  import { page } from "$app/state";
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
    initialError?: string | null;
  } = $props();

  const id = $props.id();

  let name = $state("");
  // Pre-filled when the landing page's email capture hands off here (it has
  // no waitlist backend of its own -- signup IS the real flow).
  let email = $state(page.url.searchParams.get("email") ?? "");
  let password = $state("");
  let confirmPassword = $state("");
  let loading = $state(false);
  let socialPending = $state<SocialProvider | null>(null);
  let error = $derived(initialError ?? "");
  let errorField = $state<AuthErrorField>(null);

  // Checked as you type rather than only on submit, so the mismatch is visible
  // before the button is pressed — but held back until the field has content.
  const mismatch = $derived(
    confirmPassword.length > 0 && password !== confirmPassword,
  );
  const tooShort = $derived(password.length > 0 && password.length < 8);
  const busy = $derived(loading || socialPending !== null);

  async function handleSubmit(e: Event) {
    e.preventDefault();
    if (busy) return;
    if (password !== confirmPassword) {
      error = "Las contraseñas no coinciden.";
      errorField = null;
      return;
    }
    loading = true;
    error = "";
    errorField = null;
    try {
      const { error: authError } = await authClient.signUp.email({
        email,
        password,
        name,
      });
      if (authError) {
        error = authErrorMessage(authError);
        errorField = authErrorField(authError.code);
        loading = false;
        return;
      }
      // Full reload so hooks.server.ts sees the new session cookie; `loading`
      // stays true while the page leaves.
      window.location.href = redirectTo;
    } catch {
      // A refused connection throws out of better-fetch; without this the form
      // spun "Creando cuenta…" forever with every input locked.
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
    <Label for="name-{id}" class="text-xs text-ink-dim">Nombre</Label>
    <Input
      id="name-{id}"
      type="text"
      autocomplete="name"
      placeholder="Ada Lovelace"
      required
      disabled={busy}
      bind:value={name}
      class="h-10 border-hairline-strong bg-wash text-base text-ink placeholder:text-ink-mute md:text-sm"
    />
  </div>

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
      aria-invalid={errorField === "email" || undefined}
      aria-describedby={errorField === "email" ? `error-${id}` : undefined}
      class="h-10 border-hairline-strong bg-wash text-base text-ink placeholder:text-ink-mute md:text-sm"
    />
  </div>

  <div class="flex flex-col gap-2">
    <Label for="password-{id}" class="text-xs text-ink-dim">Contraseña</Label>
    <Input
      id="password-{id}"
      type="password"
      autocomplete="new-password"
      minlength={8}
      required
      disabled={busy}
      bind:value={password}
      aria-invalid={errorField === "password" || undefined}
      aria-describedby={errorField === "password"
        ? `password-hint-${id} error-${id}`
        : `password-hint-${id}`}
      class="h-10 border-hairline-strong bg-wash text-base text-ink md:text-sm"
    />
    <span
      id="password-hint-{id}"
      class="text-2xs {tooShort ? 'text-warn' : 'text-ink-mute'}"
    >
      Mínimo 8 caracteres.
    </span>
  </div>

  <div class="flex flex-col gap-2">
    <Label for="confirm-{id}" class="text-xs text-ink-dim">
      Confirmar contraseña
    </Label>
    <Input
      id="confirm-{id}"
      type="password"
      autocomplete="new-password"
      required
      disabled={busy}
      bind:value={confirmPassword}
      aria-invalid={mismatch || undefined}
      aria-describedby={mismatch ? `mismatch-${id}` : undefined}
      class="h-10 border-hairline-strong bg-wash text-base text-ink md:text-sm {mismatch
        ? 'border-danger/50'
        : ''}"
    />
    {#if mismatch}
      <span id="mismatch-{id}" class="text-2xs text-danger-soft"
        >Las contraseñas no coinciden.</span
      >
    {/if}
  </div>

  {#if error}
    <div id="error-{id}">
      <Callout tone="danger" class="text-xs">{error}</Callout>
    </div>
  {/if}

  <Button
    type="submit"
    disabled={busy || mismatch}
    class="h-10 w-full gap-2 cta text-sm font-medium"
  >
    {#if loading}
      <Loader2 class="size-4 motion-safe:animate-spin" aria-hidden="true" />
      Creando cuenta…
    {:else}
      Crear cuenta
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
