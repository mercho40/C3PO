<!--
  This account cannot invoke skills directly — and how to tell if that is stale.

  Better Auth caches the session — role included — in a `better-auth.session_data`
  cookie for five minutes. So an account promoted to admin keeps reading as
  "not admin" until that expires, and the page has no way to know the
  difference between "you are not an admin" and "you were made one four
  minutes ago". The operator sees a hard permission error for something that
  is already fixed, in a headset, with no terminal.

  `disableCookieCache` asks the server directly, skipping the cookie; the
  reply refreshes it. `refreshAll` then re-runs the load functions so the
  layout's `isAdmin` — which is what the page reads — picks the new role up.
-->
<script lang="ts">
  import { refreshAll } from "$app/navigation";
  import { RefreshCw } from "@lucide/svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import Callout from "#lib/components/callout.svelte";
  import { authClient } from "#lib/auth-client.js";

  let refreshing = $state(false);
  /** `raw` is Better Auth's own message (English), shown verbatim. */
  let result = $state<{ text: string; raw: string | null } | null>(null);

  async function refreshRole() {
    if (refreshing) return;
    refreshing = true;
    result = null;
    try {
      const { data, error } = await authClient.getSession({
        query: { disableCookieCache: true },
      });
      if (error) {
        result = {
          text: "No se pudo verificar la sesión.",
          raw: error.message ?? null,
        };
        return;
      }
      await refreshAll();
      result =
        data?.user?.role === "admin"
          ? { text: "Listo — la cuenta es admin.", raw: null }
          : {
              text: "El servidor sigue diciendo que esta cuenta no es admin. Hay que agregarla a ADMIN_EMAILS y reiniciar el backend.",
              raw: null,
            };
    } catch (err) {
      result = {
        text: "No se pudo verificar la sesión.",
        raw: err instanceof Error ? err.message : String(err),
      };
    } finally {
      refreshing = false;
    }
  }
</script>

<Callout tone="danger" live={false} title="Esta cuenta no figura como admin">
  <p>
    <strong>La puesta en marcha y los gestos van a fallar</strong>: invocar
    habilidades directamente requiere rol admin. Caminar y girar con la cabeza
    funcionan sin admin
    <strong>solo con el puente de teleoperación conectado</strong>
    (sección «Espejo de brazos», o el botón de «Caminar»). PARAR funciona siempre,
    porque es una habilidad de seguridad.
  </p>
  <p class="mt-1 text-ink-mute">
    Si el rol se cambió recién, puede ser sólo la sesión guardada: se cachea
    hasta 5 minutos. Verificalo acá antes de tocar nada más.
  </p>
  <div class="mt-2 flex flex-wrap items-center gap-3">
    <!-- 48 px, not the `sm` the rest of the console would use: this is aimed at
         with a hand ray in the headset overlay too. -->
    <Button
      variant="outline"
      size="lg"
      class="min-h-12 gap-2 px-4"
      disabled={refreshing}
      onclick={refreshRole}
    >
      <RefreshCw class="size-4" aria-hidden="true" />
      {refreshing ? "Verificando…" : "Volver a verificar el rol"}
    </Button>
    <p role="status" class="text-xs text-ink-dim">
      {#if result}
        {result.text}
        {#if result.raw}<span lang="en" class="readout">{result.raw}</span>{/if}
      {/if}
    </p>
  </div>
</Callout>
