<script lang="ts">
  import { authClient } from "$lib/auth-client";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";
  import ThemeToggle from "$lib/components/theme-toggle.svelte";

  let { data } = $props();
</script>

<div class="flex min-h-svh items-center justify-center p-6 md:p-10">
  <Card.Root class="mx-auto w-full max-w-sm">
    {#if data.user}
      <Card.Header>
        <Card.Title class="text-2xl">Welcome back</Card.Title>
        <Card.Description>{data.user.name}</Card.Description>
      </Card.Header>
      <Card.Content class="space-y-4">
        <div class="space-y-1">
          <p class="text-sm text-muted-foreground">Email</p>
          <p class="text-sm font-medium">{data.user.email}</p>
        </div>
        <Button href="/dashboard" class="w-full">Dashboard</Button>
        <Button
          variant="outline"
          class="w-full"
          onclick={async () => {
            await authClient.signOut();
            window.location.href = "/";
          }}
        >
          Sign Out
        </Button>
      </Card.Content>
    {:else}
      <Card.Header>
        <Card.Title class="text-2xl">Welcome</Card.Title>
        <Card.Description>Get started by logging in</Card.Description>
      </Card.Header>
      <Card.Content class="space-y-4">
        <Button href="/login" class="w-full">Login</Button>
        <Button href="/signup" variant="outline" class="w-full">Sign Up</Button>
      </Card.Content>
    {/if}
    <Card.Footer class="flex-col items-center gap-2">
      <ThemeToggle />
    </Card.Footer>
  </Card.Root>
</div>
