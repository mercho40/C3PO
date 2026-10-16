<script lang="ts" module>
  export type SocialProvider = "github" | "google";
</script>

<script lang="ts">
  // Google + GitHub sign-in. Both auth forms carried their own copy of these
  // two inline SVGs and their handlers; one component keeps the provider list
  // in a single place.
  //
  // Three things this used to get wrong:
  //  - Both buttons always rendered, though neither provider had credentials —
  //    so both sent the operator off-site to a "missing client_id" error. Now
  //    only providers back reports on /health are drawn (the parent passes them
  //    in; none means no buttons and no "o" divider).
  //  - `callbackURL: "/dashboard"` is resolved by Better Auth against ITS
  //    origin (the OAuth callback is served by apps/back), so a successful
  //    login ended on the API's 404. Every URL is absolute on the web origin
  //    now, including where failures come back to.
  //  - The promise was never awaited: no pending state, and a failure did
  //    nothing visible.
  import { Loader2 } from "@lucide/svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import {
    AUTH_NETWORK_ERROR,
    authClient,
    authErrorMessage,
  } from "#lib/auth-client.js";

  let {
    providers,
    redirectTo,
    disabled = false,
    onerror,
    pending = $bindable(null),
  }: {
    providers: SocialProvider[];
    /** Sanitised same-origin path to land on after signing in. */
    redirectTo: string;
    disabled?: boolean;
    /** Spanish error for the parent form's alert. */
    onerror: (message: string) => void;
    /** Which provider is redirecting — the parent locks its form meanwhile. */
    pending?: SocialProvider | null;
  } = $props();

  const LABEL: Record<SocialProvider, string> = {
    google: "Continuar con Google",
    github: "Continuar con GitHub",
  };

  async function signIn(provider: SocialProvider) {
    if (pending) return;
    pending = provider;
    const origin = window.location.origin;
    // Failures come back to /login (Better Auth appends `?error=<code>`), with
    // the destination kept so a retry still lands in the right place.
    const back = new URL("/login", origin);
    if (redirectTo !== "/dashboard")
      back.searchParams.set("redirectTo", redirectTo);
    try {
      // Better Auth's social sign-in is the same call for sign-in and sign-up —
      // the account is created on first use — so one component serves both.
      const { error } = await authClient.signIn.social({
        provider,
        callbackURL: `${origin}${redirectTo}`,
        newUserCallbackURL: `${origin}${redirectTo}`,
        errorCallbackURL: back.toString(),
      });
      if (error) {
        onerror(authErrorMessage(error));
        pending = null;
      }
      // On success the client is already navigating to the provider; staying
      // "pending" until the page unloads is the honest state.
    } catch {
      onerror(AUTH_NETWORK_ERROR);
      pending = null;
    }
  }

  // …until the page comes BACK. Pressing Back on the provider's consent screen
  // restores this page from the back/forward cache exactly as it was left —
  // "Redirigiendo…", every button disabled, nothing in flight — and no script
  // re-runs to undo it. `persisted` is true only for that restore.
  function onpageshow(e: PageTransitionEvent) {
    if (e.persisted) pending = null;
  }
</script>

<svelte:window {onpageshow} />

{#snippet googleIcon()}
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    class="size-4"
    aria-hidden="true"
  >
    <path
      d="M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z"
      fill="currentColor"
    />
  </svg>
{/snippet}

{#snippet githubIcon()}
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="currentColor"
    class="size-4"
    aria-hidden="true"
  >
    <path
      d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"
    />
  </svg>
{/snippet}

{#if providers.length > 0}
  <div class="flex flex-col gap-2.5">
    {#each providers as provider (provider)}
      <Button
        variant="outline"
        type="button"
        disabled={disabled || pending !== null}
        onclick={() => signIn(provider)}
        class="h-10 w-full gap-2.5 tile-interactive text-sm text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50"
      >
        {#if pending === provider}
          <Loader2 class="size-4 motion-safe:animate-spin" aria-hidden="true" />
          Redirigiendo…
        {:else}
          {@render (provider === "google" ? googleIcon : githubIcon)()}
          {LABEL[provider]}
        {/if}
      </Button>
    {/each}
  </div>
{/if}
