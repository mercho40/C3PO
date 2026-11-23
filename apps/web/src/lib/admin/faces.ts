/**
 * The «Caras» page's calls to apps/back (routes/face.ts) and their Spanish.
 *
 * Seeing a face is confirmed like an action: «Ver cara», «Borrar», «Agregar
 * persona», «Hacer operador/superadmin» and each switch first open a card on back (`askFace`), the operator
 * presses Confirmar, and only then is the call made with that card
 * (`runFace`). PARAR voids open cards on back, so a Confirmar after a stop is
 * refused and the operator asks again.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import type { FacePerson, FaceRole, FaceSettings, FacesView } from "@repo/back";
import { apiAt, unreached } from "#lib/api-client.js";

export type FaceAction = "settings" | "delete" | "enroll" | "photo" | "role";
// The bridge's /face/people payload, typed by back's schema (bridge/payloads.ts).
export type { FacePerson, FaceRole, FaceSettings, FacesView };

export type Failed = { ok: false; text: string };

export const SWITCHES: { key: keyof FaceSettings; label: string }[] = [
  { key: "greet", label: "Saludar a conocidos" },
  { key: "greet_unknown", label: "Saludar a desconocidos" },
  { key: "listen_after_greet", label: "Escuchar después de saludar" },
  { key: "gate", label: "Pedir cara para acciones peligrosas" },
];

export const faceRoleLabel = (role: string) =>
  role === "superadmin" ? "Superadmin" : "Operador";

const STATUS_TEXT: Record<number, string> = {
  401: "Tu sesión venció. Volvé a iniciar sesión.",
  403: "Solo los admins gestionan las caras.",
  404: "Esa persona ya no está registrada.",
  409: "La confirmación era para otra acción o persona. Volvé a pedirla.",
  422: "Datos inválidos: revisá el nombre y el rol.",
  428: "La confirmación venció o se anuló (PARAR la anula). Volvé a pedirla.",
  502: "No se pudo contactar al robot.",
  503: "El backend no tiene SAFETY_CONFIRM_SECRET: no puede confirmar acciones.",
  504: "El robot tardó demasiado en responder.",
};

/**
 * Why the bridge did not enroll, by its `code` (bridge /face/enroll), in
 * Spanish. Anything else is shown as the bridge's own `reason`.
 */
const REASON_TEXT: Record<string, string> = {
  no_live_face:
    "No se vio una sola cara viva en cada imagen (¿otra persona en cuadro, una foto, demasiado lejos?). Probá de nuevo agachándote: la cara a la altura del pecho del robot, a 0,5–0,8 m.",
  not_same_person:
    "Las imágenes no eran todas de la misma persona: que quede una sola frente a la cámara.",
  camera_unavailable:
    "El robot no pudo usar la cámara o los modelos de reconocimiento (el log del bridge dice por qué).",
  exists:
    "Ya hay alguien registrado con ese nombre. Volvé a agregarla para reemplazarla.",
  busy: "Ya hay un registro en curso: esperá a que termine.",
  invalid: "Datos inválidos: revisá el nombre y el rol.",
};

/** A refused or failed answer from back, in Spanish. */
export function faceErrorText(status: number | null, body: unknown): string {
  const b = (body ?? {}) as {
    code?: unknown;
    reason?: unknown;
    error?: unknown;
  };
  if (typeof b.code === "string" && REASON_TEXT[b.code])
    return REASON_TEXT[b.code];
  if (typeof b.reason === "string" && b.reason)
    return `El robot respondió: ${b.reason}`;
  if (status === null) return "No se pudo contactar al backend.";
  return (
    STATUS_TEXT[status] ??
    `Error ${status}${typeof b.error === "string" ? ` (${b.error})` : ""}.`
  );
}

const sameName = (a: string, b: string) =>
  a.trim().replace(/\s+/g, " ").toLocaleLowerCase() ===
  b.trim().replace(/\s+/g, " ").toLocaleLowerCase();

/**
 * The «Agregar persona» card. A name already enrolled (any case) asks
 * «Ya existe <nombre>: ¿reemplazar?», and only that card carries
 * `replace: true`: back binds it, so it cannot be spent on anything else.
 */
export function enrollCard(
  name: string,
  role: FaceRole,
  people: FacePerson[],
): {
  args: { name: string; role: FaceRole; replace: boolean };
  title: string;
  text: string;
  cta: string;
  danger: boolean;
} {
  const look =
    "Al confirmar, el robot mira por la cámara de la cabeza durante 5–10 s: la persona se agacha con la cara a la altura del pecho del robot, a 0,5–0,8 m, sin el casco (la cámara mira hacia abajo).";
  const existing = people.find((p) => sameName(p.name, name));
  if (existing)
    return {
      args: { name, role, replace: true },
      title: `Ya existe ${existing.name}: ¿reemplazar?`,
      text: `Se borra su registro y su foto, y se guarda el de quien esté frente a la cámara, como ${faceRoleLabel(role).toLowerCase()}. ${look}`,
      cta: "Reemplazar",
      danger: true,
    };
  return {
    args: { name, role, replace: false },
    title: `¿Agregar a ${name} como ${faceRoleLabel(role).toLowerCase()}?`,
    text: look,
    cta: "Agregar",
    danger: false,
  };
}

const day = new Intl.DateTimeFormat("es-AR", { dateStyle: "medium" });

/**
 * The enrolled date; the bridge may send ISO text or epoch seconds, and Eden
 * hands ISO text over as a `Date` (api-client.ts).
 */
export function enrolledText(
  at: string | number | Date | null | undefined,
): string {
  if (at == null || at === "") return "—";
  const d = new Date(typeof at === "number" && at < 1e12 ? at * 1000 : at);
  return Number.isNaN(d.getTime()) ? "—" : day.format(d);
}

/** An Eden answer, as these calls read it: no answer at all is `status: null`. */
type Reply = {
  data: unknown;
  error: { status: unknown; value: unknown } | null;
};

function failed(r: Reply | null): Failed {
  if (!r || unreached(r.error))
    return { ok: false, text: faceErrorText(null, null) };
  return {
    ok: false,
    text: faceErrorText(r.error?.status as number, r.error?.value),
  };
}

/** Eden throws only for a JSON body it could not parse: no answer worth the name. */
const settle = <R extends Reply>(p: Promise<R>): Promise<R | null> =>
  p.catch(() => null);

export async function loadFaces(
  base: string,
  f: typeof fetch = fetch,
): Promise<{ ok: true; view: FacesView } | Failed> {
  const r = await settle(
    apiAt(base, f).face.people.get({ fetch: { cache: "no-store" } }),
  );
  return r && !r.error && r.data ? { ok: true, view: r.data } : failed(r);
}

/** Open the Confirmar card for exactly this action and arguments. */
export async function askFace(
  base: string,
  action: FaceAction,
  args: Record<string, unknown>,
  f: typeof fetch = fetch,
): Promise<{ ok: true; id: string } | Failed> {
  const r = await settle(
    apiAt(base, f).face.confirmations.post({ action, args }),
  );
  return r && !r.error && r.data?.id ? { ok: true, id: r.data.id } : failed(r);
}

/** «Cancelar»: the card is voided on back too. Best effort: it expires anyway. */
export async function cancelFace(
  base: string,
  id: string,
  f: typeof fetch = fetch,
): Promise<void> {
  await settle(
    apiAt(base, f)
      .face.confirmations({ id: encodeURIComponent(id) })
      .delete(),
  );
}

/**
 * «Confirmar»: the call itself, carrying the card. A photo comes back as a
 * Blob, fetched with `no-store` so no cache keeps a copy of the face — a plain
 * `fetch`, because Eden reads an image as text.
 */
export async function runFace(
  base: string,
  action: FaceAction,
  args: Record<string, unknown>,
  confirmation: string,
  f: typeof fetch = fetch,
): Promise<{ ok: true; body: unknown; photo: Blob | null } | Failed> {
  const person = encodeURIComponent(String(args.id ?? ""));
  if (action === "photo") {
    let res: Response | null = null;
    try {
      res = await f(
        `${base}/face/people/${person}/photo.jpg?confirmation=${encodeURIComponent(confirmation)}`,
        { credentials: "include", cache: "no-store" },
      );
    } catch {
      // handled below
    }
    if (res?.ok) return { ok: true, body: null, photo: await res.blob() };
    const body = await res?.json().catch(() => null);
    return { ok: false, text: faceErrorText(res?.status ?? null, body) };
  }
  const face = apiAt(base, f).face;
  const query = { query: { confirmation } };
  const r = await settle<Reply>(
    action === "settings"
      ? face.settings.post(args, query)
      : action === "enroll"
        ? face.enroll.post(args, query)
        : action === "delete"
          ? face.people({ id: person }).delete(undefined, query)
          : face.people({ id: person }).patch({ role: args.role }, query),
  );
  if (!r || r.error) return failed(r);
  // An enroll the bridge refused can still be a 200 carrying `reason`.
  const reason = (r.data as { reason?: unknown } | null)?.reason;
  if (typeof reason === "string")
    return { ok: false, text: faceErrorText(200, r.data) };
  return { ok: true, body: r.data, photo: null };
}
