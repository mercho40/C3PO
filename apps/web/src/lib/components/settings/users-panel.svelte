<!--
  "Usuarios" — admin user management, over Better Auth's admin plugin.

  The plugin was already mounted on both ends (`authClient.admin.*`) and
  enforces permissions itself; it just had no UI, so the only way to make an
  admin was ADMIN_EMAILS plus a backend restart. Three things it does NOT do,
  handled here:

   - stop an admin from locking themselves out (demoting the last admin is
     unrecoverable from the product) — self-actions are refused before they are
     sent (`selfActionBlock`);
   - explain that ADMIN_EMAILS re-grants admin at every backend boot, so a
     demotion of a listed account lasts only until the next restart;
   - say that ban/revoke reach an open session only when its 5-minute cookie
     cache lapses.

  Every role change, ban and session revoke asks first. Outcomes are toasts
  (one-shot actions); the list reloads from the server afterwards rather than
  being patched optimistically.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/state";
  import { toast } from "svelte-sonner";
  import { authClient } from "#lib/auth-client.js";
  import * as AlertDialog from "#lib/components/ui/alert-dialog/index.js";
  import Callout from "#lib/components/callout.svelte";
  import {
    USERS_PAGE,
    adminErrorText,
    isAdminRole,
    roleLabel,
    selfActionBlock,
    selfRowNote,
    type SelfAction,
  } from "#lib/admin/users.js";

  type AdminUser = {
    id: string;
    name: string;
    email: string;
    role?: string | null;
    banned?: boolean | null;
    banReason?: string | null;
    createdAt: Date | string;
  };

  type Pending = {
    kind: "promote" | "demote" | "ban" | "unban" | "revoke";
    user: AdminUser;
  };

  const myId = $derived(page.data.user?.id ?? null);

  let users = $state.raw<AdminUser[]>([]);
  let total = $state(0);
  let offset = $state(0);
  let search = $state("");
  let searchField = $state<"email" | "name">("email");
  let loading = $state(false);
  let loaded = $state(false);
  let error = $state<{ text: string; verbatim: string | null } | null>(null);

  let pending = $state<Pending | null>(null);
  let dialogOpen = $state(false);
  let banReason = $state("");
  let acting = $state(false);

  const joined = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium" });

  async function load() {
    if (loading) return;
    loading = true;
    error = null;
    try {
      const term = search.trim();
      const { data, error: err } = await authClient.admin.listUsers({
        query: {
          limit: USERS_PAGE,
          offset,
          sortBy: "createdAt",
          sortDirection: "desc",
          ...(term
            ? { searchValue: term, searchField, searchOperator: "contains" }
            : {}),
        },
      });
      if (err || !data) {
        error = adminErrorText(err);
        return;
      }
      users = data.users as AdminUser[];
      total = data.total;
      loaded = true;
    } catch (e) {
      error = adminErrorText({
        status: 0,
        message: e instanceof Error ? e.message : null,
      });
    } finally {
      loading = false;
    }
  }

  function runSearch(event: SubmitEvent) {
    event.preventDefault();
    offset = 0;
    void load();
  }

  function turnPage(delta: number) {
    offset = Math.max(0, offset + delta * USERS_PAGE);
    void load();
  }

  function ask(next: Pending) {
    pending = next;
    banReason = "";
    dialogOpen = true;
  }

  const TITLE: Record<Pending["kind"], string> = {
    promote: "¿Hacer admin a esta cuenta?",
    demote: "¿Quitarle el rol de admin?",
    ban: "¿Bloquear esta cuenta?",
    unban: "¿Desbloquear esta cuenta?",
    revoke: "¿Cerrar todas sus sesiones?",
  };

  const CONFIRM: Record<Pending["kind"], string> = {
    promote: "Hacer admin",
    demote: "Quitar admin",
    ban: "Bloquear",
    unban: "Desbloquear",
    revoke: "Cerrar sesiones",
  };

  async function confirm() {
    const p = pending;
    if (!p || acting) return;
    acting = true;
    try {
      const userId = p.user.id;
      const res =
        p.kind === "promote"
          ? await authClient.admin.setRole({ userId, role: "admin" })
          : p.kind === "demote"
            ? await authClient.admin.setRole({ userId, role: "user" })
            : p.kind === "ban"
              ? await authClient.admin.banUser({
                  userId,
                  ...(banReason.trim() ? { banReason: banReason.trim() } : {}),
                })
              : p.kind === "unban"
                ? await authClient.admin.unbanUser({ userId })
                : await authClient.admin.revokeUserSessions({ userId });
      if (res.error) {
        const e = adminErrorText(res.error);
        toast.error(
          e.text,
          e.verbatim ? { description: e.verbatim } : undefined,
        );
        return;
      }
      const who = p.user.name || p.user.email;
      toast.success(
        {
          promote: `${who} ahora es admin`,
          demote: `${who} ahora es operador`,
          ban: `${who} quedó bloqueado`,
          unban: `${who} quedó desbloqueado`,
          revoke: `Se cerraron las sesiones de ${who}`,
        }[p.kind],
        {
          description:
            p.kind === "promote" || p.kind === "demote"
              ? "Le llega en hasta 5 minutos, o al tocar «Actualizar rol»."
              : p.kind === "ban" || p.kind === "revoke"
                ? "Una sesión abierta puede seguir hasta 5 minutos (caché de la cookie)."
                : undefined,
        },
      );
      dialogOpen = false;
      await load();
    } catch (e) {
      const err = adminErrorText({
        status: 0,
        message: e instanceof Error ? e.message : null,
      });
      toast.error(
        err.text,
        err.verbatim ? { description: err.verbatim } : undefined,
      );
    } finally {
      acting = false;
    }
  }

  onMount(() => {
    void load();
  });
</script>

<!--
  A self-blocked action stays FOCUSABLE (`aria-disabled`, not `disabled`) and
  points at the visible sentence under the row, so a keyboard or screen-reader
  user reaches the reason instead of a silently skipped control. A `disabled`
  button is removed from the tab order and its `title` is never announced.
-->
{#snippet actionButton(
  label: string,
  onclick: () => void,
  blocked: string | null,
  noteId: string | null,
  danger = false,
)}
  <button
    type="button"
    onclick={() => {
      if (blocked === null) onclick();
    }}
    disabled={acting}
    aria-disabled={blocked !== null ? "true" : undefined}
    aria-describedby={blocked !== null && noteId ? noteId : undefined}
    class={[
      "inline-flex h-9 items-center rounded-lg border px-2.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:opacity-40",
      danger
        ? "border-danger/30 text-danger-soft hover:bg-danger/10 aria-disabled:hover:bg-transparent"
        : "border-hairline-strong text-ink-dim hover:border-accent-edge hover:text-ink aria-disabled:hover:border-hairline-strong aria-disabled:hover:text-ink-dim",
    ]}
  >
    {label}
  </button>
{/snippet}

<section
  class="flex min-w-0 flex-col gap-4 panel p-5"
  aria-labelledby="users-heading"
>
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <h2 id="users-heading" class="eyebrow">Usuarios</h2>
    <span class="readout">Solo admins</span>
  </div>

  <div class="flex flex-col gap-1 text-sm leading-relaxed text-ink-mute">
    <p>
      Solo los admins ejecutan habilidades directamente (botones de VR, Decir) y
      ven el registro de comandos. Los operadores manejan el robot a través del
      agente, y todos pueden usar PARAR.
    </p>
    <p>
      Las cuentas de <span class="font-mono text-xs">ADMIN_EMAILS</span> vuelven a
      ser admin cada vez que se reinicia el backend: quitarles el rol acá dura hasta
      el próximo reinicio.
    </p>
  </div>

  <form class="flex flex-wrap items-end gap-2" onsubmit={runSearch}>
    <div class="flex min-w-0 flex-1 basis-48 flex-col gap-1.5">
      <label for="users-search" class="text-xs text-ink-dim">Buscar</label>
      <input
        id="users-search"
        type="search"
        bind:value={search}
        autocomplete="off"
        placeholder={searchField === "email" ? "correo@ejemplo.com" : "Nombre"}
        class="h-10 w-full tile px-3 text-base text-ink placeholder:text-ink-mute focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
      />
    </div>
    <div class="flex flex-col gap-1.5">
      <label for="users-field" class="text-xs text-ink-dim">En</label>
      <select
        id="users-field"
        bind:value={searchField}
        class="h-10 tile px-3 text-base text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
      >
        <option value="email">Correo</option>
        <option value="name">Nombre</option>
      </select>
    </div>
    <button
      type="submit"
      disabled={loading}
      class="inline-flex h-10 items-center rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
    >
      Buscar
    </button>
  </form>

  {#if error}
    <Callout tone="danger" title="No se pudo leer la lista de usuarios">
      {error.text}
      {#if error.verbatim}<span class="block readout" lang="en"
          >{error.verbatim}</span
        >{/if}
    </Callout>
  {/if}

  <div aria-busy={loading}>
    {#if !loaded && loading}
      <p class="text-sm text-ink-mute" role="status">Cargando usuarios…</p>
    {:else if loaded && users.length === 0}
      <p class="text-sm text-ink-mute" role="status">
        Ninguna cuenta coincide.
      </p>
    {:else}
      <ul class="flex flex-col gap-2">
        {#each users as u (u.id)}
          {@const admin = isAdminRole(u.role)}
          {@const me = u.id === myId}
          {@const blockDemote = admin
            ? selfActionBlock(myId, u.id, "demote")
            : null}
          {@const blockBan = u.banned
            ? null
            : selfActionBlock(myId, u.id, "ban")}
          {@const blockRevoke = selfActionBlock(myId, u.id, "revoke")}
          {@const note = selfRowNote(
            [
              blockDemote ? "demote" : null,
              blockBan ? "ban" : null,
              blockRevoke ? "revoke" : null,
            ].filter((a): a is SelfAction => a !== null),
          )}
          {@const noteId = note ? `self-note-${u.id}` : null}
          <li
            class="flex flex-col gap-3 tile px-3 py-3 lg:flex-row lg:items-center lg:justify-between"
          >
            <div class="flex min-w-0 flex-col gap-1">
              <p class="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span class="text-sm break-words text-ink"
                  >{u.name || "Sin nombre"}</span
                >
                {#if me}<span class="readout text-ink-dim">(vos)</span>{/if}
                <span
                  class={[
                    "rounded-sm border px-1.5 readout font-medium tracking-[0.12em] uppercase",
                    admin
                      ? "border-accent-edge text-ink-dim"
                      : "border-hairline-strong text-ink-mute",
                  ]}
                >
                  {roleLabel(u.role)}
                </span>
                {#if u.banned}
                  <span
                    class="rounded-sm border border-danger/35 px-1.5 readout font-medium tracking-[0.12em] text-danger-soft uppercase"
                  >
                    Bloqueado
                  </span>
                {/if}
              </p>
              <p class="text-xs break-all text-ink-mute">{u.email}</p>
              {#if u.banned && u.banReason}
                <p class="text-xs text-ink-mute">Motivo: {u.banReason}</p>
              {/if}
              <p class="readout">Alta {joined.format(new Date(u.createdAt))}</p>
            </div>
            <div class="flex min-w-0 flex-col gap-2 lg:items-end">
              <div class="flex flex-wrap gap-1.5">
                {#if admin}
                  {@render actionButton(
                    "Quitar admin",
                    () => ask({ kind: "demote", user: u }),
                    blockDemote,
                    noteId,
                  )}
                {:else}
                  {@render actionButton(
                    "Hacer admin",
                    () => ask({ kind: "promote", user: u }),
                    null,
                    null,
                  )}
                {/if}
                {#if u.banned}
                  {@render actionButton(
                    "Desbloquear",
                    () => ask({ kind: "unban", user: u }),
                    null,
                    null,
                  )}
                {:else}
                  {@render actionButton(
                    "Bloquear",
                    () => ask({ kind: "ban", user: u }),
                    blockBan,
                    noteId,
                    true,
                  )}
                {/if}
                {@render actionButton(
                  "Cerrar sesiones",
                  () => ask({ kind: "revoke", user: u }),
                  blockRevoke,
                  noteId,
                )}
              </div>
              {#if note && noteId}
                <p
                  id={noteId}
                  class="max-w-sm text-xs text-ink-mute lg:text-right"
                >
                  {note}
                </p>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  {#if loaded && total > USERS_PAGE}
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="readout">
        {offset + 1}–{Math.min(offset + users.length, total)} de {total}
      </span>
      <div class="flex gap-2">
        <button
          type="button"
          disabled={loading || offset === 0}
          onclick={() => turnPage(-1)}
          class="inline-flex h-9 items-center rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40"
        >
          Anterior
        </button>
        <button
          type="button"
          disabled={loading || offset + USERS_PAGE >= total}
          onclick={() => turnPage(1)}
          class="inline-flex h-9 items-center rounded-lg border border-hairline-strong px-3 text-sm text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40"
        >
          Siguiente
        </button>
      </div>
    </div>
  {:else if loaded}
    <p class="readout">{total} {total === 1 ? "cuenta" : "cuentas"}</p>
  {/if}
</section>

<AlertDialog.Root bind:open={dialogOpen}>
  <AlertDialog.Content>
    {#if pending}
      <AlertDialog.Header>
        <AlertDialog.Title>{TITLE[pending.kind]}</AlertDialog.Title>
        <AlertDialog.Description>
          <span class="font-medium text-ink"
            >{pending.user.name || pending.user.email}</span
          >
          ({pending.user.email}).
          {#if pending.kind === "promote"}
            Va a poder ejecutar habilidades directamente sobre el robot, ver el
            registro de comandos y administrar usuarios.
          {:else if pending.kind === "demote"}
            Va a dejar de poder ejecutar habilidades directamente; seguirá
            manejando el robot a través del agente.
          {:else if pending.kind === "ban"}
            No va a poder iniciar sesión, y sus sesiones se cierran (una sesión
            abierta puede durar hasta 5 minutos más).
          {:else if pending.kind === "unban"}
            Va a poder volver a iniciar sesión.
          {:else}
            Tendrá que volver a iniciar sesión en todos sus dispositivos (puede
            tardar hasta 5 minutos en una sesión abierta).
          {/if}
        </AlertDialog.Description>
      </AlertDialog.Header>
      {#if pending.kind === "ban"}
        <div class="flex flex-col gap-1.5">
          <label for="ban-reason" class="text-xs text-ink-dim"
            >Motivo (opcional)</label
          >
          <input
            id="ban-reason"
            bind:value={banReason}
            maxlength="200"
            class="h-10 w-full tile px-3 text-base text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
          />
        </div>
      {/if}
      <AlertDialog.Footer>
        <AlertDialog.Cancel disabled={acting}>Cancelar</AlertDialog.Cancel>
        <AlertDialog.Action
          variant={pending.kind === "promote" || pending.kind === "unban"
            ? "secondary"
            : "destructive"}
          disabled={acting}
          onclick={confirm}
        >
          {acting ? "Aplicando…" : CONFIRM[pending.kind]}
        </AlertDialog.Action>
      </AlertDialog.Footer>
    {/if}
  </AlertDialog.Content>
</AlertDialog.Root>
