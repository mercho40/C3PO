<script lang="ts">
  import { Loader2, TriangleAlert } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Separator } from "$lib/components/ui/separator/index.js";
  import SocialAuthButtons from "./social-auth-buttons.svelte";
  import { authClient } from "$lib/auth-client";

  const id = $props.id();

  let name = $state("");
  let email = $state("");
  let password = $state("");
  let confirmPassword = $state("");
  let loading = $state(false);
  let error = $state("");

  // Checked as you type rather than only on submit, so the mismatch is visible
  // before the button is pressed — but held back until the field has content.
  const mismatch = $derived(
    confirmPassword.length > 0 && password !== confirmPassword,
  );
  const tooShort = $derived(password.length > 0 && password.length < 8);

  async function handleSubmit(e: Event) {
    e.preventDefault();
    if (password !== confirmPassword) {
      error = "Las contraseñas no coinciden.";
      return;
    }
    loading = true;
    error = "";
    await authClient.signUp.email(
      { email, password, name },
      {
        onSuccess: () => {
          window.location.href = "/dashboard";
        },
        onError: (ctx) => {
          error = ctx.error.message;
        },
      },
    );
    loading = false;
  }
</script>

<form onsubmit={handleSubmit} class="flex flex-col gap-4">
  <div class="flex flex-col gap-2">
    <Label for="name-{id}" class="text-xs text-ink-dim">Nombre</Label>
    <Input
      id="name-{id}"
      type="text"
      autocomplete="name"
      placeholder="Ada Lovelace"
      required
      disabled={loading}
      bind:value={name}
      class="h-10 border-hairline-strong bg-wash text-sm text-ink placeholder:text-ink-mute"
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
      disabled={loading}
      bind:value={email}
      class="h-10 border-hairline-strong bg-wash text-sm text-ink placeholder:text-ink-mute"
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
      disabled={loading}
      bind:value={password}
      aria-describedby="password-hint-{id}"
      class="h-10 border-hairline-strong bg-wash text-sm text-ink"
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
      disabled={loading}
      bind:value={confirmPassword}
      aria-invalid={mismatch}
      class="h-10 border-hairline-strong bg-wash text-sm text-ink {mismatch
        ? 'border-danger/50'
        : ''}"
    />
    {#if mismatch}
      <span class="text-2xs text-danger-soft"
        >Las contraseñas no coinciden.</span
      >
    {/if}
  </div>

  {#if error}
    <p
      class="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/[0.06] px-3 py-2 text-xs text-danger-soft"
      role="alert"
    >
      <TriangleAlert class="mt-px size-3.5 shrink-0" />
      {error}
    </p>
  {/if}

  <Button
    type="submit"
    disabled={loading || mismatch}
    class="h-10 w-full gap-2 cta text-sm font-medium"
  >
    {#if loading}
      <Loader2 class="size-4 animate-spin" />
      Creando cuenta…
    {:else}
      Crear cuenta
    {/if}
  </Button>

  <div class="flex items-center gap-3 py-1">
    <Separator class="flex-1 bg-hairline" />
    <span class="eyebrow">o</span>
    <Separator class="flex-1 bg-hairline" />
  </div>

  <SocialAuthButtons disabled={loading} />
</form>
