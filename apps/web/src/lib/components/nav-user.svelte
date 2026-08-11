<script lang="ts">
  import {
    ChevronsUpDown,
    LogOut,
    Settings,
    UserRound,
  } from "@lucide/svelte";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { authClient } from "$lib/auth-client";
  import * as AlertDialog from "$lib/components/ui/alert-dialog/index.js";
  import * as Avatar from "$lib/components/ui/avatar/index.js";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu/index.js";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import { useSidebar } from "$lib/components/ui/sidebar/index.js";

  const sidebar = useSidebar();

  let logoutDialogOpen = $state(false);
  let loggingOut = $state(false);

  async function logout() {
    if (loggingOut) return;
    loggingOut = true;
    await authClient.signOut();
    await goto("/login");
  }

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

<Sidebar.Menu>
  <Sidebar.MenuItem>
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>
        {#snippet child({ props })}
          <Sidebar.MenuButton
            {...props}
            size="lg"
            class="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
          >
            <Avatar.Root class="size-8 shrink-0 rounded-lg">
              {#if user?.image}
                <Avatar.Image src={user.image} alt={displayName} class="rounded-lg" />
              {/if}
              <Avatar.Fallback
                class="rounded-lg bg-gradient-to-br from-[#9fc5ff] to-[#4a7dd1] font-mono text-[11px] font-bold text-[#06121c]"
              >
                {initials}
              </Avatar.Fallback>
            </Avatar.Root>
            <div class="grid flex-1 text-start leading-tight">
              <span class="truncate text-[13px] font-medium text-[#eaf1ff]">{displayName}</span>
              {#if user?.email && user.email !== displayName}
                <span class="truncate text-[11px] text-[#8a96ad]">{user.email}</span>
              {/if}
            </div>
            <ChevronsUpDown class="ms-auto size-3.5 shrink-0 text-[#8a96ad]" />
          </Sidebar.MenuButton>
        {/snippet}
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        class="w-(--bits-dropdown-menu-anchor-width) min-w-56 rounded-lg"
        side={sidebar.isMobile ? "bottom" : "right"}
        align="end"
        sideOffset={4}
      >
        <DropdownMenu.Label class="p-0 font-normal">
          <div class="flex items-center gap-2 px-1 py-1.5 text-start text-sm">
            <Avatar.Root class="size-8 shrink-0 rounded-lg">
              {#if user?.image}
                <Avatar.Image src={user.image} alt={displayName} class="rounded-lg" />
              {/if}
              <Avatar.Fallback
                class="rounded-lg bg-gradient-to-br from-[#9fc5ff] to-[#4a7dd1] font-mono text-[11px] font-bold text-[#06121c]"
              >
                {initials}
              </Avatar.Fallback>
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
  </Sidebar.MenuItem>
</Sidebar.Menu>

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
