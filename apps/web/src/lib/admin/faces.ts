/**
 * The «Caras» page's calls to apps/back (routes/face.ts) and their Spanish.
 *
 * Seeing a face is confirmed like an action: «Ver cara», «Borrar», «Agregar
 * persona» and each switch first open a card on back (`askFace`), the operator
 * presses Confirmar, and only then is the call made with that card
 * (`runFace`). PARAR voids open cards on back, so a Confirmar after a stop is
 * refused and the operator asks again.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

export type FaceAction = "settings" | "delete" | "enroll" | "photo";
export type FaceRole = "superadmin" | "operator";
export type FaceSettings = {
  greet: boolean;
  greet_unknown: boolean;
  gate: boolean;
};
export type FacePerson = {
  id: string;
  name: string;
  role: FaceRole;
  enrolled_at: string | number | null;
  has_photo: boolean;
};
export type FacesView = {
  settings: FaceSettings;
  people: FacePerson[];
  unknown_seen: { count: number; last_age_s: number | null };
  models: boolean;
};

export type Failed = { ok: false; text: string };

export const SWITCHES: { key: keyof FaceSettings; label: string }[] = [
  { key: "greet", label: "Saludar a conocidos" },
  { key: "greet_unknown", label: "Saludar a desconocidos" },
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
    "No se vio una sola cara viva en cada imagen (¿otra persona en cuadro, una foto, demasiado lejos?). Probá de nuevo a 0,5–1 m.",
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
    "Al confirmar, el robot mira por la cámara de la cabeza durante 5–10 s: la persona tiene que estar parada a 0,5–1 m, sin el casco.";
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

/** The enrolled date; the bridge may send ISO text or epoch seconds. */
export function enrolledText(at: string | number | null | undefined): string {
  if (at == null || at === "") return "—";
  const d = new Date(typeof at === "number" && at < 1e12 ? at * 1000 : at);
  return Number.isNaN(d.getTime()) ? "—" : day.format(d);
}

async function send(
  f: typeof fetch,
  url: string,
  init: RequestInit = {},
): Promise<Response | null> {
  try {
    return await f(url, { credentials: "include", ...init });
  } catch {
    return null;
  }
}

const postJson = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function loadFaces(
  base: string,
  f: typeof fetch = fetch,
): Promise<{ ok: true; view: FacesView } | Failed> {
  const res = await send(f, `${base}/face/people`, { cache: "no-store" });
  const body = await res?.json().catch(() => null);
  return res?.ok
    ? { ok: true, view: body as FacesView }
    : { ok: false, text: faceErrorText(res?.status ?? null, body) };
}

/** Open the Confirmar card for exactly this action and arguments. */
export async function askFace(
  base: string,
  action: FaceAction,
  args: Record<string, unknown>,
  f: typeof fetch = fetch,
): Promise<{ ok: true; id: string } | Failed> {
  const res = await send(
    f,
    `${base}/face/confirmations`,
    postJson({ action, args }),
  );
  const body = (await res?.json().catch(() => null)) as { id?: string } | null;
  return res?.ok && body?.id
    ? { ok: true, id: body.id }
    : { ok: false, text: faceErrorText(res?.status ?? null, body) };
}

/** «Cancelar»: the card is voided on back too. Best effort: it expires anyway. */
export async function cancelFace(
  base: string,
  id: string,
  f: typeof fetch = fetch,
): Promise<void> {
  await send(f, `${base}/face/confirmations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/**
 * «Confirmar»: the call itself, carrying the card. A photo comes back as a
 * Blob, fetched with `no-store` so no cache keeps a copy of the face.
 */
export async function runFace(
  base: string,
  action: FaceAction,
  args: Record<string, unknown>,
  confirmation: string,
  f: typeof fetch = fetch,
): Promise<{ ok: true; body: unknown; photo: Blob | null } | Failed> {
  const c = `confirmation=${encodeURIComponent(confirmation)}`;
  const person = encodeURIComponent(String(args.id ?? ""));
  const res =
    action === "settings"
      ? await send(f, `${base}/face/settings?${c}`, postJson(args))
      : action === "enroll"
        ? await send(f, `${base}/face/enroll?${c}`, postJson(args))
        : action === "delete"
          ? await send(f, `${base}/face/people/${person}?${c}`, {
              method: "DELETE",
            })
          : await send(f, `${base}/face/people/${person}/photo.jpg?${c}`, {
              cache: "no-store",
            });
  if (res?.ok && action === "photo")
    return { ok: true, body: null, photo: await res.blob() };
  const body = await res?.json().catch(() => null);
  // An enroll the bridge refused can still be a 200 carrying `reason`.
  const reason = (body as { reason?: unknown } | null)?.reason;
  if (!res?.ok || typeof reason === "string")
    return { ok: false, text: faceErrorText(res?.status ?? null, body) };
  return { ok: true, body, photo: null };
}
