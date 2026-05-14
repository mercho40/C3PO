<script lang="ts">
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import * as Field from "$lib/components/ui/field/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { authClient } from "$lib/auth-client";

  let name = $state("");
  let email = $state("");
  let password = $state("");
  let confirmPassword = $state("");
  let loading = $state(false);
  let error = $state("");

  async function handleSubmit(e: Event) {
    e.preventDefault();
    if (password !== confirmPassword) {
      error = "Passwords do not match";
      return;
    }
    loading = true;
    error = "";
    await authClient.signUp.email(
      { email, password, name },
      {
        onSuccess: () => {
          window.location.href = "/";
        },
        onError: (ctx) => {
          error = ctx.error.message;
        },
      },
    );
    loading = false;
  }
</script>

<Card.Root class="mx-auto w-full max-w-sm">
  <Card.Header>
    <Card.Title>Create an account</Card.Title>
    <Card.Description
      >Enter your information below to create your account</Card.Description
    >
  </Card.Header>
  <Card.Content>
    <form onsubmit={handleSubmit}>
      <Field.Group>
        <Field.Field>
          <Field.Label for="name">Full Name</Field.Label>
          <Input
            id="name"
            type="text"
            placeholder="John Doe"
            required
            bind:value={name}
          />
        </Field.Field>
        <Field.Field>
          <Field.Label for="email">Email</Field.Label>
          <Input
            id="email"
            type="email"
            placeholder="m@example.com"
            required
            bind:value={email}
          />
        </Field.Field>
        <Field.Field>
          <Field.Label for="password">Password</Field.Label>
          <Input id="password" type="password" required bind:value={password} />
          <Field.Description
            >Must be at least 8 characters long.</Field.Description
          >
        </Field.Field>
        <Field.Field>
          <Field.Label for="confirm-password">Confirm Password</Field.Label>
          <Input
            id="confirm-password"
            type="password"
            required
            bind:value={confirmPassword}
          />
        </Field.Field>
        {#if error}
          <p class="text-sm text-red-500">{error}</p>
        {/if}
        <Field.Group>
          <Field.Field>
            <Button type="submit" class="w-full" disabled={loading}>
              {loading ? "Creating account..." : "Create Account"}
            </Button>
            <Button
              variant="outline"
              class="w-full"
              type="button"
              onclick={() => authClient.signIn.social({ provider: "google" })}
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                <path
                  d="M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z"
                  fill="currentColor"
                />
              </svg>
              Sign up with Google
            </Button>
            <Button
              variant="outline"
              class="w-full"
              type="button"
              onclick={() => authClient.signIn.social({ provider: "github" })}
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="currentColor"
              >
                <path
                  d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"
                />
              </svg>
              Sign up with GitHub
            </Button>
            <Field.Description class="px-6 text-center">
              Already have an account? <a href="/login" class="underline"
                >Sign in</a
              >
            </Field.Description>
          </Field.Field>
        </Field.Group>
      </Field.Group>
    </form>
  </Card.Content>
</Card.Root>
