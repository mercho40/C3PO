<script lang="ts">
  import { authClient } from "$lib/auth-client";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Card from "$lib/components/ui/card/index.js";

  let { data } = $props();
</script>

<div class="flex min-h-svh items-center justify-center p-6 md:p-10">
  <Card.Root class="mx-auto w-full max-w-sm">
    <Card.Header>
      <Card.Title class="text-2xl">Dashboard</Card.Title>
      <Card.Description>Welcome back, {data.user?.name}</Card.Description>
    </Card.Header>
    <Card.Content class="space-y-4">
      <div class="space-y-1">
        <p class="text-sm text-muted-foreground">Email</p>
        <p class="text-sm font-medium">{data.user?.email}</p>
      </div>
      <div class="space-y-1">
        <p class="text-sm text-muted-foreground">Member since</p>
        <p class="text-sm font-medium">
          {data.user?.createdAt
            ? new Date(data.user.createdAt).toLocaleDateString()
            : ""}
        </p>
      </div>
      <Button
        class="w-full"
        variant="outline"
        onclick={async () => {
          await authClient.signOut();
          window.location.href = "/login";
        }}
      >
        Sign Out
      </Button>
    </Card.Content>
    <Card.Footer>
      <a href="/" class="text-sm text-muted-foreground underline"
        >Back to home</a
      >
    </Card.Footer>
  </Card.Root>
</div>
