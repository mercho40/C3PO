/**
 * What to tell the operator when an agent turn fails.
 *
 * `DefaultChatTransport` turns a non-2xx answer into `new Error(responseBody)`,
 * and an error mid-stream arrives as whatever routes/agent.ts's `onError`
 * passed through — deliberately the real cause, not the SDK's masked "An error
 * occurred." So `chat.error.message` is raw English: "Failed after 3 attempts.
 * Last error: Bad Gateway", "AGENT_API_KEY is not set — …", a bare "Conflict"
 * for a 409, or the start of an nginx HTML page. The page used to print that
 * in one truncated line. Now it leads with a Spanish sentence, says what to do
 * when the status is known, and keeps the raw cause — cleaned, never clipped —
 * for whoever has to debug it.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

export type ChatErrorView = {
  /** Always the same lead, so the banner reads the same way every time. */
  lead: string;
  /** What this failure means and what to do, Spanish. Null when unknown. */
  hint: string | null;
  /** The raw cause (often English), tags stripped. Null when empty. */
  cause: string | null;
  /** Whether retrying the same request could possibly help. */
  retryable: boolean;
  /** The fix is a different conversation (403 foreign chat, 409 voice chat). */
  suggestNewChat: boolean;
};

export const ERROR_LEAD = "El agente no pudo completar el turno.";

/** Longest cause shown. An nginx page or a stack trace is not worth a screen. */
const CAUSE_MAX = 600;

const GATEWAY =
  /bad gateway|service unavailable|gateway time-?out|failed after \d+ attempts|econnrefused|econnreset|upstream/i;
const NETWORK =
  /failed to fetch|networkerror|load failed|network request failed/i;

/** Strip markup and collapse whitespace; cap pathological lengths. */
export function cleanCause(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let text = raw;
  if (/<\/?[a-z][^>]*>/i.test(text)) {
    text = text
      .replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ")
      .replace(/<[^>]+>/g, " ");
  }
  text = text.replace(/\s+/g, " ").trim();
  if (!text) return null;
  return text.length > CAUSE_MAX ? `${text.slice(0, CAUSE_MAX - 1)}…` : text;
}

/**
 * @param message `chat.error.message`.
 * @param httpStatus the status of the failed POST /agent when it never started
 *   streaming; null for errors that arrived inside a 200 stream, or when no
 *   HTTP answer came back at all.
 */
export function describeChatError(
  message: string | null | undefined,
  httpStatus: number | null,
): ChatErrorView {
  const cause = cleanCause(message);
  const view = (
    hint: string | null,
    retryable: boolean,
    suggestNewChat = false,
  ): ChatErrorView => ({
    lead: ERROR_LEAD,
    hint,
    cause,
    retryable,
    suggestNewChat,
  });

  if (httpStatus === 401) {
    return view("Tu sesión venció. Iniciá sesión de nuevo.", false);
  }
  if (httpStatus === 403) {
    return view(
      "Esta conversación es de otra cuenta. Empezá un chat nuevo.",
      false,
      true,
    );
  }
  if (httpStatus === 409) {
    return view(
      "Esta conversación es una transcripción de voz y es de solo lectura. Para escribirle al robot, empezá un chat nuevo.",
      false,
      true,
    );
  }
  if (message && /AGENT_API_KEY/.test(message)) {
    return view(
      "El agente no está configurado en el servidor: falta la clave de TIC AI (AGENT_API_KEY).",
      false,
    );
  }
  if (
    httpStatus === 502 ||
    httpStatus === 503 ||
    httpStatus === 504 ||
    (message && GATEWAY.test(message))
  ) {
    return view(
      "El servidor de IA (TIC AI) no respondió o se cayó a mitad del turno. Probá de nuevo en unos segundos.",
      true,
    );
  }
  if (httpStatus === 422 || httpStatus === 400) {
    return view("El servidor rechazó el pedido como inválido.", false);
  }
  if (httpStatus !== null && httpStatus >= 500) {
    return view("Falló el servidor de la consola.", true);
  }
  if (httpStatus === null && message && NETWORK.test(message)) {
    return view(
      "No se pudo contactar al servidor de la consola. Revisá la conexión.",
      true,
    );
  }
  return view(null, true);
}
