<!--
  /settings — "Configuración".

  The sidebar entry said "Pronto" and the user menu's "Configuración" did
  nothing, while Better Auth's admin plugin was already wired on both ends with
  no UI. Three sections: your account (with the role-refresh the 5-minute
  cookie cache makes necessary), what this console is pointed at, and — for
  admins — user management.

  The page title comes from the topbar (`#lib/nav.ts`).
-->
<script lang="ts">
  import AccountPanel from "#lib/components/settings/account-panel.svelte";
  import ConsolePanel from "#lib/components/settings/console-panel.svelte";
  import UsersPanel from "#lib/components/settings/users-panel.svelte";

  let { data } = $props();
</script>

<div class="flex flex-col gap-4 pb-2">
  <div class="grid gap-4 lg:grid-cols-2">
    <AccountPanel />
    <ConsolePanel />
  </div>

  {#if data.isAdmin}
    <UsersPanel />
  {/if}
</div>
