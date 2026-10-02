<!--
  "Cuenta" — who you are signed in as, your role, and signing out.

  THE ROLE IS UP TO FIVE MINUTES BEHIND. Better Auth caches the session (role
  included) in a signed cookie for 5 min, and both the console and the
  backend's admin checks read that cache. So an account promoted a minute ago
  still reads as "Operador" here, and as non-admin to /skills/:name/invoke.
  "Actualizar rol" asks the server directly (`disableCookieCache`), which also
  re-issues the cookie, then re-runs the loads so every page sees it — the same
  recovery /vr-control offers, for the same reason.
-->
<script lang="ts">
  import { goto, invalidateAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { toast } from "svelte-sonner";
  import { authClient } from "$lib/auth-client";
  import { isAdminRole, roleLabel } from "$lib/admin/users";

  const user = $derived(page.data.user);
  const admin = $derived(isAdminRole(user?.role));

  let refreshing = $state(false);
  let signingOut = $state(false);

  async function refreshRole() {
    if (refreshing) return;
    refreshing = true;
    try {
      const { data, error } = await authClient.getSession({
        query: { disableCookieCache: true },
      });
      if (error) {
        toast.error("No se pudo verificar el rol", {
          description: error.message ?? "Error de sesión.",
        });
        return;
      }
      if (!data) {
        toast.error("Tu sesión venció", {
          description: "Volvé a iniciar sesión.",
        });
        return;
      }
      await invalidateAll();
      toast.success(`Rol verificado: ${roleLabel(data.user.role)}`, {
        description: isAdminRole(data.user.role)
          ? undefined
          : "Si esperabas ser admin, pedíselo a un admin (Configuración → Usuarios) o agregá tu correo a ADMIN_EMAILS y reiniciá el backend.",
      });
    } catch (err) {
      toast.error("No se pudo verificar el rol", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      refreshing = false;
    }
  }

  async function signOut() {
    if (signingOut) return;
    signingOut = true;
    try {
      const { error } = await authClient.signOut();
      if (error) {
        toast.error("No se pudo cerrar la sesión", {
          description: error.message ?? "El servidor rechazó el pedido.",
        });
        return;
      }
      // `invalidateAll`: without it the root layout keeps the stale user and
      // /login bounces straight back to /dashboard.
      await goto(resolve("/login"), { invalidateAll: true });
    } catch (err) {
      toast.error("No se pudo cerrar la sesión", {
        description:
          err instanceof Error
            ? err.message
            : "No se pudo contactar al servidor.",
      });
    } finally {
      signingOut = false;
    }
  }
</script>

<section
  class="flex min-w-0 flex-col gap-4 panel p-5"
  aria-labelledby="account-heading"
>
  <h2 id="account-heading" class="eyebrow">Cuenta</h2>

  <dl class="grid gap-3 sm:grid-cols-2">
    <div class="flex min-w-0 flex-col gap-1">
      <dt class="eyebrow">Nombre</dt>
      <dd class="text-sm break-words text-ink">{user?.name || "—"}</dd>
    </div>
    <div class="flex min-w-0 flex-col gap-1">
      <dt class="eyebrow">Correo</dt>
      <dd class="text-sm break-all text-ink">{user?.email ?? "—"}</dd>
    </div>
    <div class="flex min-w-0 flex-col gap-1">
      <dt class="eyebrow">Rol</dt>
      <dd>
        <span
          class={[
            "inline-block rounded-sm border px-1.5 readout font-medium tracking-[0.12em] uppercase",
            admin
              ? "border-accent-edge text-ink-dim"
              : "border-hairline-strong text-ink-mute",
          ]}
        >
          {roleLabel(user?.role)}
        </span>
      </dd>
    </div>
  </dl>

  <p class="text-sm leading-relaxed text-ink-mute">
    {#if admin}
      Como admin podés ejecutar habilidades directamente (botones de VR, Decir)
      y administrar usuarios.
    {:else}
      Como operador manejás el robot a través del agente; las órdenes directas
      (botones de VR, Decir) son solo para admins. PARAR funciona para todos.
    {/if}
    Un cambio de rol llega a la consola en hasta 5 minutos, porque la sesión se guarda
    en una cookie. Si te lo cambiaron recién, actualizalo.
  </p>

  <div class="flex flex-wrap gap-2">
    <button
      type="button"
      onclick={refreshRole}
      disabled={refreshing}
      class="inline-flex h-10 items-center rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
    >
      {refreshing ? "Verificando…" : "Actualizar rol"}
    </button>
    <button
      type="button"
      onclick={signOut}
      disabled={signingOut}
      class="inline-flex h-10 items-center rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-danger/40 hover:text-danger-soft focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
    >
      {signingOut ? "Cerrando sesión…" : "Cerrar sesión"}
    </button>
  </div>
</section>
