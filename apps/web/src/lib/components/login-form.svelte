<script lang="ts">
  import { Loader2, TriangleAlert } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { Separator } from "$lib/components/ui/separator/index.js";
  import SocialAuthButtons from "./social-auth-buttons.svelte";
  import { authClient } from "$lib/auth-client";

  const id = $props.id();

  let email = $state("");
  let password = $state("");
  let loading = $state(false);
  let error = $state("");

  async function handleSubmit(e: Event) {
    e.preventDefault();
    loading = true;
    error = "";
    await authClient.signIn.email(
      { email, password },
      {
        onSuccess: () => {
          // Full reload rather than goto(): the session cookie has just been
          // set, and hooks.server.ts reads it during SSR.
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
      autocomplete="current-password"
      required
      disabled={loading}
      bind:value={password}
      class="h-10 border-hairline-strong bg-wash text-sm text-ink"
    />
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
    disabled={loading}
    class="h-10 w-full gap-2 cta text-sm font-medium"
  >
    {#if loading}
      <Loader2 class="size-4 animate-spin" />
      Ingresando…
    {:else}
      Ingresar
    {/if}
  </Button>

  <div class="flex items-center gap-3 py-1">
    <Separator class="flex-1 bg-hairline" />
    <span class="eyebrow">o</span>
    <Separator class="flex-1 bg-hairline" />
  </div>

  <SocialAuthButtons disabled={loading} />
</form>
