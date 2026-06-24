<script lang="ts">
  import {
    Activity,
    Map,
    Video,
    MessageSquare,
    Mic,
    HeartPulse,
    Settings,
    UserRound,
    LogOut,
    ChevronsUpDown,
  } from "@lucide/svelte";
  import { page } from "$app/state";
  import { goto } from "$app/navigation";
  import { authClient } from "$lib/auth-client";
  import * as Avatar from "$lib/components/ui/avatar/index.js";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu/index.js";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Separator } from "$lib/components/ui/separator/index.js";

  let logoutDialogOpen = $state(false);
  let loggingOut = $state(false);

  async function logout() {
    if (loggingOut) return;
    loggingOut = true;
    await authClient.signOut();
    await goto("/login");
  }

  const navItems = [
    { label: "Inicio", href: "/dashboard", icon: Activity },
    { label: "Mapa en vivo", href: "/live-map", icon: Map },
    { label: "Cámaras", href: "/live-camera", icon: Video },
    { label: "Chat", href: "/chat", icon: MessageSquare },
  ];

  // Routes not built yet — shown but disabled so they don't 404.
  const pendingItems = [
    { label: "Control por voz", icon: Mic },
    { label: "Salud", icon: HeartPulse },
  ];

  const user = $derived(page.data.user);
  const displayName = $derived(String(user?.name ?? user?.email ?? "Perfil"));
  const initials = $derived(
    displayName
      .split(/\s+/)
      .map((part) => part[0] ?? "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "C3",
  );
</script>

<aside
  class="flex h-full w-60 flex-col gap-2 border-r border-[rgba(180,210,255,0.08)] bg-[rgba(5,7,13,0.6)] px-6 py-6 backdrop-blur-sm"
>
  <a href="/dashboard" class="mb-3 inline-flex w-fit">
    <img
      src="/logo.svg"
      alt="C3PO"
      class="h-9 w-auto object-contain drop-shadow-[0_0_12px_rgba(126,229,255,0.45)]"
    />
  </a>

  <nav class="flex flex-col gap-1">
    {#each navItems as item (item.href)}
      {@const Icon = item.icon}
      {@const active = page.url.pathname === item.href}
      <a
        href={item.href}
        class="relative flex items-center gap-3 rounded-lg border px-3 py-2.5 text-[13px] transition-colors {active
          ? 'border-[rgba(159,197,255,0.25)] bg-[rgba(159,197,255,0.14)] text-[#c6dcff]'
          : 'border-transparent text-[#8a96ad] hover:bg-[rgba(159,197,255,0.06)] hover:text-[#c6dcff]'}"
      >
        {#if active}
          <span
            class="absolute top-2 left-[-1px] h-5 w-0.5 rounded-sm bg-[#9ae5f8] shadow-[0px_0px_12px_rgba(126,229,255,0.55)]"
          ></span>
        {/if}
        <Icon class="size-3.5 shrink-0" />
        <span>{item.label}</span>
      </a>
    {/each}
    {#each pendingItems as item (item.label)}
      {@const Icon = item.icon}
      <span
        aria-disabled="true"
        class="relative flex cursor-not-allowed items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-[13px] text-[#8a96ad]/40"
      >
        <Icon class="size-3.5 shrink-0" />
        <span>{item.label}</span>
        <Badge
          variant="outline"
          class="ml-auto border-[rgba(180,210,255,0.18)] px-1.5 py-0 text-[9px] tracking-wide text-[#8a96ad] uppercase"
          >Pronto</Badge
        >
      </span>
    {/each}
  </nav>

  <Separator class="my-2 bg-[rgba(180,210,255,0.08)]" />

  <div class="px-2">
    <span class="font-mono text-[9px] tracking-[0.22em] text-[#8a96ad]/70 uppercase"
      >Sistema</span
    >
  </div>
  <span
    aria-disabled="true"
    class="flex cursor-not-allowed items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-[13px] text-[#8a96ad]/40"
  >
    <Settings class="size-3.5 shrink-0" />
    <span>Configuración</span>
    <Badge
      variant="outline"
      class="ml-auto border-[rgba(180,210,255,0.18)] px-1.5 py-0 text-[9px] tracking-wide text-[#8a96ad] uppercase"
      >Pronto</Badge
    >
  </span>

  <DropdownMenu.Root>
    <DropdownMenu.Trigger>
      {#snippet child({ props })}
        <button
          {...props}
          class="mt-auto flex w-full items-center gap-2.5 rounded-[10px] p-2 text-start transition-colors hover:bg-[rgba(159,197,255,0.07)] data-[state=open]:bg-[rgba(159,197,255,0.1)]"
        >
          <Avatar.Root class="size-8 shrink-0 rounded-lg">
            {#if user?.image}
              <Avatar.Image src={user.image} alt={displayName} class="rounded-lg" />
            {/if}
            <Avatar.Fallback
              class="rounded-lg bg-gradient-to-br from-[#9fc5ff] to-[#4a7dd1] font-mono text-[11px] font-bold text-[#06121c]"
              >{initials}</Avatar.Fallback
            >
          </Avatar.Root>
          <div class="grid flex-1 text-start leading-tight">
            <span class="truncate text-[13px] font-medium text-[#eaf1ff]">{displayName}</span>
            {#if user?.email && user.email !== displayName}
              <span class="truncate text-[11px] text-[#8a96ad]">{user.email}</span>
            {/if}
          </div>
          <ChevronsUpDown class="size-3.5 shrink-0 text-[#8a96ad]" />
        </button>
      {/snippet}
    </DropdownMenu.Trigger>
    <DropdownMenu.Content side="right" align="end" sideOffset={8} class="min-w-56 rounded-lg">
      <DropdownMenu.Label class="p-0 font-normal">
        <div class="flex items-center gap-2 px-1 py-1.5 text-start text-sm">
          <Avatar.Root class="size-8 shrink-0 rounded-lg">
            {#if user?.image}
              <Avatar.Image src={user.image} alt={displayName} class="rounded-lg" />
            {/if}
            <Avatar.Fallback
              class="rounded-lg bg-gradient-to-br from-[#9fc5ff] to-[#4a7dd1] font-mono text-[11px] font-bold text-[#06121c]"
              >{initials}</Avatar.Fallback
            >
          </Avatar.Root>
          <div class="grid flex-1 text-start text-sm leading-tight">
            <span class="truncate font-medium">{displayName}</span>
            {#if user?.email && user.email !== displayName}
              <span class="truncate text-xs text-muted-foreground">{user.email}</span>
            {/if}
          </div>
        </div>
      </DropdownMenu.Label>
      <DropdownMenu.Separator />
      <DropdownMenu.Group>
        <DropdownMenu.Item>
          <UserRound />
          Cuenta
        </DropdownMenu.Item>
        <DropdownMenu.Item>
          <Settings />
          Configuración
        </DropdownMenu.Item>
      </DropdownMenu.Group>
      <DropdownMenu.Separator />
      <DropdownMenu.Item variant="destructive" onSelect={() => (logoutDialogOpen = true)}>
        <LogOut />
        Cerrar sesión
      </DropdownMenu.Item>
    </DropdownMenu.Content>
  </DropdownMenu.Root>
</aside>

<AlertDialog.Root bind:open={logoutDialogOpen}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>¿Cerrar sesión?</AlertDialog.Title>
      <AlertDialog.Description>
        Se cerrará tu sesión en C3PO y volverás a la pantalla de inicio de sesión.
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel disabled={loggingOut}>Cancelar</AlertDialog.Cancel>
      <AlertDialog.Action variant="destructive" disabled={loggingOut} onclick={logout}>
        <LogOut />
        Cerrar sesión
      </AlertDialog.Action>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
