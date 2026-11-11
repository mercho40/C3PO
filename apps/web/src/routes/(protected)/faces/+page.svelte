<!--
  /faces — «Caras»: who the robot knows by face. Admins only.

  Seeing a face is confirmed like an action. Every switch, «Ver cara»,
  «Borrar» and «Agregar persona» first opens a card on back
  (`#lib/admin/faces.ts`), shown here as a Confirmar dialog; only Confirmar
  makes the call, once, with that card. PARAR voids open cards on back.

  A photo is fetched with `no-store`, shown from an object URL, and that URL is
  revoked when it is closed, replaced, or the page is left.

  The page title comes from the topbar (`#lib/nav.ts`).
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { RefreshCw } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { PUBLIC_API_URL } from "$app/env/public";
  import * as AlertDialog from "#lib/components/ui/alert-dialog/index.js";
  import Callout from "#lib/components/callout.svelte";
  import { formatAge } from "#lib/robot/format.js";
  import {
    SWITCHES,
    askFace,
    cancelFace,
    enrolledText,
    faceRoleLabel,
    loadFaces,
    runFace,
    type FaceAction,
    type FacePerson,
    type FaceRole,
    type FacesView,
  } from "#lib/admin/faces.js";

  let { data } = $props();

  type Card = {
    action: FaceAction;
    args: Record<string, unknown>;
    id: string;
    title: string;
    text: string;
    cta: string;
    danger: boolean;
  };

  let view = $state.raw<FacesView | null>(null);
  let loadError = $state<string | null>(null);
  let loading = $state(false);
  let card = $state<Card | null>(null);
  let open = $state(false);
  let asking = $state(false);
  let busy = $state<FaceAction | null>(null);
  let photo = $state<{ url: string; personId: string } | null>(null);
  let enrollName = $state("");
  let enrollRole = $state<FaceRole>("operator");
  let enrollResult = $state<{ ok: boolean; text: string } | null>(null);

  async function refresh() {
    if (loading) return;
    loading = true;
    const r = await loadFaces(PUBLIC_API_URL);
    loading = false;
    if (r.ok) {
      view = r.view;
      loadError = null;
    } else loadError = r.text;
  }

  async function ask(
    action: FaceAction,
    args: Record<string, unknown>,
    copy: Omit<Card, "action" | "args" | "id">,
  ) {
    if (asking || busy) return;
    asking = true;
    const r = await askFace(PUBLIC_API_URL, action, args);
    asking = false;
    if (!r.ok) {
      toast.error(r.text);
      return;
    }
    card = { action, args, id: r.id, ...copy };
    open = true;
  }

  // Escape, the overlay or «Cancelar»: the card is voided on back as well.
  function onOpenChange(next: boolean) {
    if (next || !card) return;
    void cancelFace(PUBLIC_API_URL, card.id);
    card = null;
  }

  function closePhoto() {
    if (photo) URL.revokeObjectURL(photo.url);
    photo = null;
  }

  async function confirm() {
    const c = card;
    if (!c || busy) return;
    // Cleared before closing, so closing does not cancel the card being used.
    card = null;
    open = false;
    busy = c.action;
    if (c.action === "enroll") enrollResult = null;
    const r = await runFace(PUBLIC_API_URL, c.action, c.args, c.id);
    busy = null;
    if (!r.ok) {
      if (c.action === "enroll") enrollResult = { ok: false, text: r.text };
      else toast.error(r.text);
      return;
    }
    if (c.action === "photo" && r.photo) {
      closePhoto();
      photo = {
        url: URL.createObjectURL(r.photo),
        personId: String(c.args.id),
      };
      return;
    }
    if (c.action === "enroll") {
      enrollResult = { ok: true, text: `Se agregó a ${c.args.name}.` };
      enrollName = "";
    } else if (c.action === "delete") {
      if (photo?.personId === c.args.id) closePhoto();
      toast.success("Persona borrada");
    } else toast.success("Listo");
    await refresh();
  }

  function askSwitch(key: (typeof SWITCHES)[number], current: boolean) {
    void ask(
      "settings",
      { [key.key]: !current },
      {
        title: `¿${current ? "Desactivar" : "Activar"} «${key.label}»?`,
        text: current
          ? "El robot deja de hacerlo hasta que lo vuelvas a activar."
          : "El robot empieza a hacerlo en cuanto confirmes.",
        cta: current ? "Desactivar" : "Activar",
        danger: false,
      },
    );
  }

  function askPhoto(p: FacePerson) {
    void ask(
      "photo",
      { id: p.id },
      {
        title: `¿Ver la cara de ${p.name}?`,
        text: "Se muestra solo en esta pantalla, no se guarda, y queda registrado que la viste.",
        cta: "Ver cara",
        danger: false,
      },
    );
  }

  function askDelete(p: FacePerson) {
    void ask(
      "delete",
      { id: p.id },
      {
        title: `¿Borrar a ${p.name}?`,
        text: "El robot deja de reconocerla. Para volver a agregarla hay que registrarla de nuevo frente a la cámara.",
        cta: "Borrar",
        danger: true,
      },
    );
  }

  function askEnroll(event: SubmitEvent) {
    event.preventDefault();
    const name = enrollName.trim();
    if (!name) return;
    void ask(
      "enroll",
      { name, role: enrollRole },
      {
        title: `¿Agregar a ${name} como ${faceRoleLabel(enrollRole).toLowerCase()}?`,
        text: "Al confirmar, el robot mira por la cámara de la cabeza durante 5–10 s: la persona tiene que estar parada a 0,5–1 m, sin el casco.",
        cta: "Agregar",
        danger: false,
      },
    );
  }

  onMount(() => {
    if (data.isAdmin) void refresh();
    return closePhoto;
  });
</script>

{#if !data.isAdmin}
  <Callout tone="warn" title="Solo admins">
    Las caras que reconoce el robot las gestionan los admins.
  </Callout>
{:else}
  <div class="flex flex-col gap-4 pb-2">
    {#if loadError}
      <Callout tone="danger" title="No se pudo leer las caras">
        {loadError}
      </Callout>
    {/if}
    {#if view && !view.models}
      <Callout tone="warn" title="Reconocimiento apagado">
        Los modelos de reconocimiento no están cargados en el robot: no reconoce
        ni agrega caras.
      </Callout>
    {/if}

    <div class="grid gap-4 lg:grid-cols-2">
      <section
        class="flex min-w-0 flex-col gap-3 panel p-5"
        aria-labelledby="faces-settings-heading"
      >
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="faces-settings-heading" class="eyebrow">Comportamiento</h2>
          <button
            type="button"
            onclick={refresh}
            disabled={loading}
            aria-label="Actualizar"
            class="inline-flex size-9 items-center justify-center rounded-lg border border-hairline-strong text-ink-dim transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
          >
            <RefreshCw
              class={["size-4", loading && "motion-safe:animate-spin"]}
            />
          </button>
        </div>
        {#each SWITCHES as s (s.key)}
          {@const on = view?.settings[s.key] ?? false}
          <div class="flex items-center justify-between gap-3 tile px-3 py-2.5">
            <span class="text-sm text-ink" id="switch-{s.key}">{s.label}</span>
            <button
              type="button"
              role="switch"
              aria-checked={on}
              aria-labelledby="switch-{s.key}"
              disabled={!view || asking || busy !== null}
              onclick={() => askSwitch(s, on)}
              class={[
                "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50",
                on
                  ? "border-accent-edge bg-peri/40"
                  : "border-hairline-strong bg-transparent",
              ]}
            >
              <span
                class={[
                  "inline-block size-4 rounded-full bg-ink transition-transform",
                  on ? "translate-x-6" : "translate-x-1",
                ]}
              ></span>
            </button>
          </div>
        {/each}
        {#if view}
          <p class="readout" role="status">
            Caras desconocidas vistas: {view.unknown_seen.count}
            {#if view.unknown_seen.count > 0}
              · la última {formatAge(view.unknown_seen.last_age_s)}{/if}
          </p>
        {/if}
      </section>

      <section
        class="flex min-w-0 flex-col gap-3 panel p-5"
        aria-labelledby="faces-enroll-heading"
      >
        <h2 id="faces-enroll-heading" class="eyebrow">Agregar persona</h2>
        <p class="text-sm leading-relaxed text-ink-mute">
          Pararse a 0,5–1 m frente a la cámara de la cabeza, sin el casco.
        </p>
        <form class="flex flex-wrap items-end gap-2" onsubmit={askEnroll}>
          <div class="flex min-w-0 flex-1 basis-40 flex-col gap-1.5">
            <label for="enroll-name" class="text-xs text-ink-dim">Nombre</label>
            <input
              id="enroll-name"
              bind:value={enrollName}
              maxlength="80"
              autocomplete="off"
              class="h-10 w-full tile px-3 text-base text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
            />
          </div>
          <div class="flex flex-col gap-1.5">
            <label for="enroll-role" class="text-xs text-ink-dim">Rol</label>
            <select
              id="enroll-role"
              bind:value={enrollRole}
              class="h-10 tile px-3 text-base text-ink focus-visible:border-accent-edge focus-visible:ring-2 focus-visible:ring-cyan-hot/40 focus-visible:outline-none md:text-sm"
            >
              <option value="operator">Operador</option>
              <option value="superadmin">Superadmin</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={!enrollName.trim() || asking || busy !== null}
            class="inline-flex h-10 items-center rounded-lg cta px-3 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50"
          >
            Agregar persona
          </button>
        </form>
        {#if busy === "enroll"}
          <p class="text-sm text-ink" role="status">
            Mirando a la cámara… (5–10 s). Que la persona no se mueva.
          </p>
        {:else if enrollResult}
          <p
            class={[
              "text-sm",
              enrollResult.ok ? "text-ink" : "text-danger-soft",
            ]}
            role="status"
          >
            {enrollResult.text}
          </p>
        {/if}
      </section>
    </div>

    <section
      class="flex min-w-0 flex-col gap-3 panel p-5"
      aria-labelledby="faces-people-heading"
    >
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="faces-people-heading" class="eyebrow">Personas</h2>
        <span class="readout">Solo admins</span>
      </div>
      {#if !view && loading}
        <p class="text-sm text-ink-mute" role="status">Cargando…</p>
      {:else if view && view.people.length === 0}
        <p class="text-sm text-ink-mute" role="status">
          Nadie registrado todavía.
        </p>
      {:else if view}
        <ul class="flex flex-col gap-2">
          {#each view.people as p (p.id)}
            <li class="flex flex-col gap-3 tile px-3 py-3">
              <div
                class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div class="flex min-w-0 flex-col gap-1">
                  <p class="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span class="text-sm break-words text-ink">{p.name}</span>
                    <span
                      class={[
                        "rounded-sm border px-1.5 readout font-medium tracking-[0.12em] uppercase",
                        p.role === "superadmin"
                          ? "border-accent-edge text-ink-dim"
                          : "border-hairline-strong text-ink-mute",
                      ]}
                    >
                      {faceRoleLabel(p.role)}
                    </span>
                  </p>
                  <p class="readout">Alta {enrolledText(p.enrolled_at)}</p>
                </div>
                <div class="flex flex-wrap gap-1.5">
                  {#if photo?.personId === p.id}
                    <button
                      type="button"
                      onclick={closePhoto}
                      class="inline-flex h-9 items-center rounded-lg border border-hairline-strong px-2.5 text-xs text-ink-dim transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
                    >
                      Ocultar cara
                    </button>
                  {:else}
                    <button
                      type="button"
                      disabled={!p.has_photo || asking || busy !== null}
                      onclick={() => askPhoto(p)}
                      class="inline-flex h-9 items-center rounded-lg border border-hairline-strong px-2.5 text-xs text-ink-dim transition-colors hover:border-accent-edge hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40"
                    >
                      {busy === "photo" ? "Abriendo…" : "Ver cara"}
                    </button>
                  {/if}
                  <button
                    type="button"
                    disabled={asking || busy !== null}
                    onclick={() => askDelete(p)}
                    class="inline-flex h-9 items-center rounded-lg border border-danger/30 px-2.5 text-xs text-danger-soft transition-colors hover:bg-danger/10 focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-40"
                  >
                    Borrar
                  </button>
                </div>
              </div>
              {#if photo?.personId === p.id}
                <img
                  src={photo.url}
                  alt="Cara registrada de {p.name}"
                  class="max-h-64 w-auto self-start rounded-lg border border-hairline-strong"
                />
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </div>
{/if}

<AlertDialog.Root bind:open {onOpenChange}>
  <AlertDialog.Content>
    {#if card}
      <AlertDialog.Header>
        <AlertDialog.Title>{card.title}</AlertDialog.Title>
        <AlertDialog.Description>
          {card.text} Si alguien toca PARAR antes, esta confirmación se anula.
        </AlertDialog.Description>
      </AlertDialog.Header>
      <AlertDialog.Footer>
        <AlertDialog.Cancel>Cancelar</AlertDialog.Cancel>
        <AlertDialog.Action
          variant={card.danger ? "destructive" : "secondary"}
          onclick={confirm}
        >
          Confirmar · {card.cta}
        </AlertDialog.Action>
      </AlertDialog.Footer>
    {/if}
  </AlertDialog.Content>
</AlertDialog.Root>
