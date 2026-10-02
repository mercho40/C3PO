<script lang="ts">
  // Account menu at the foot of the sidebar.
  //
  // Logout used to sit behind a confirmation dialog while high-danger motion ran
  // on one tap — confirmation friction spent on the one action here that cannot
  // hurt anyone. It is a plain menu action now. It also had no error handling:
  // a failed signOut left both dialog buttons disabled until a reload, and a
  // successful one navigated without `invalidateAll`, so the root layout kept a
  // stale `user` and /login bounced to /dashboard and back.
  import { ChevronsUpDown, LogOut, Settings } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { authClient } from "$lib/auth-client";
  import * as Avatar from "$lib/components/ui/avatar/index.js";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu/index.js";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import { useSidebar } from "$lib/components/ui/sidebar/index.js";

  const sidebar = useSidebar();

  let loggingOut = $state(false);

  async function logout() {
    if (loggingOut) return;
    loggingOut = true;
    try {
      const { error } = await authClient.signOut();
      if (error) {
        // An HTTP error means the session is still valid server-side —
        // navigating to /login now would only bounce straight back.
        toast.error("No se pudo cerrar la sesión", {
          description: "El servidor rechazó el pedido. Probá de nuevo.",
        });
        return;
      }
      // invalidateAll: re-run the root layout load so `user` clears before
      // /login renders.
      await goto(resolve("/login"), { invalidateAll: true });
    } catch {
      toast.error("No se pudo cerrar la sesión", {
        description:
          "No se pudo contactar al servidor. Tu sesión sigue abierta.",
      });
    } finally {
      loggingOut = false;
    }
  }

  const user = $derived(page.data.user);
  // From the (protected) layout — the session cookie cache, so up to 5 minutes
  // behind a role change. Direct skill invocation is admin-only server-side;
  // without this, the only way to find out you cannot drive was a 403.
  const isAdmin = $derived(page.data.isAdmin === true);
  const role = $derived(isAdmin ? "Admin" : "Operador");
  const roleHint = $derived(
    isAdmin
      ? "Puede invocar habilidades directamente"
      : "Sin control directo: el robot se maneja a través del agente; PARAR funciona siempre",
  );
  const displayName = $derived(String(user?.name ?? user?.email ?? "Perfil"));
  const subline = $derived(
    user?.email && user.email !== displayName
      ? `${role} · ${user.email}`
      : role,
  );
  const initials = $derived(
    displayName
      .split(/\s+/)
      .map((part) => part[0] ?? "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "C3",
  );
</script>

{#snippet avatar()}
  <Avatar.Root class="size-8 shrink-0 rounded-lg">
    {#if user?.image}
      <Avatar.Image src={user.image} alt={displayName} class="rounded-lg" />
    {/if}
    <Avatar.Fallback
      class="rounded-lg bg-gradient-to-br from-peri to-deep-blue font-mono text-2xs font-bold text-primary-foreground"
    >
      {initials}
    </Avatar.Fallback>
  </Avatar.Root>
{/snippet}

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
            {@render avatar()}
            <div class="grid flex-1 text-start leading-tight">
              <span class="truncate text-sm font-medium text-ink"
                >{displayName}</span
              >
              <span class="truncate text-2xs text-ink-mute" title={roleHint}>
                {subline}
              </span>
            </div>
            <ChevronsUpDown class="ms-auto size-3.5 shrink-0 text-ink-mute" />
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
            {@render avatar()}
            <div class="grid flex-1 text-start text-sm leading-tight">
              <span class="truncate font-medium">{displayName}</span>
              {#if user?.email && user.email !== displayName}
                <span class="truncate text-xs text-ink-mute">{user.email}</span>
              {/if}
            </div>
          </div>
          <p class="px-1 pb-1.5 text-xs text-ink-mute">
            <span
              class={[
                "me-1.5 rounded-sm border px-1.5 readout font-medium tracking-[0.12em] uppercase",
                isAdmin
                  ? "border-accent-edge text-ink-dim"
                  : "border-hairline-strong text-ink-mute",
              ]}>{role}</span
            >
            {roleHint}
          </p>
        </DropdownMenu.Label>
        <DropdownMenu.Separator />
        <DropdownMenu.Item>
          {#snippet child({ props })}
            <a href={resolve("/settings")} {...props}>
              <Settings />
              Configuración
            </a>
          {/snippet}
        </DropdownMenu.Item>
        <DropdownMenu.Separator />
        <DropdownMenu.Item
          variant="destructive"
          disabled={loggingOut}
          onSelect={() => void logout()}
        >
          <LogOut />
          {loggingOut ? "Cerrando sesión…" : "Cerrar sesión"}
        </DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  </Sidebar.MenuItem>
</Sidebar.Menu>
