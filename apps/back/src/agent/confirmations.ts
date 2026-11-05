/**
 * Calls the bridge refused for want of an operator's confirmation, waiting for
 * one.
 *
 * A refused T2 / high-danger-T1 call is NOTED here by the agent's tool (and the
 * realtime voice session). It is never confirmed from here: only the admin
 * routes `POST /skills/:name/confirm` (a button) and `POST /voice/transcribe`
 * (the admin's own push-to-talk) take one, and they then grant AND run that
 * exact call themselves (routes/skills.ts `confirmPending`). So the model never
 * retries after a confirmation, it is told the result.
 *
 * In memory on purpose: a back restart forgets every pending call, and the
 * operator asks again. Nothing here can grant anything; the bridge's own
 * argument-bound 30 s grant is still the gate.
 */

export type PendingConfirmation = {
  /** The refused call's toolCallId: one card, one call. */
  id: string;
  tool: string;
  args: Record<string, unknown>;
  /** The admin whose turn asked for it; only they may confirm it. */
  ownerId: string;
  /** The chat it was asked in; null for the legacy voice loop. */
  chatId: string | null;
  /** Asked by the legacy voice loop: its outcome is fed back there. */
  loop: boolean;
  at: number;
};

/** How long a refusal stays confirmable. Older, the world has moved on: ask again. */
export const PENDING_TTL_MS = 120_000;

const pending = new Map<string, PendingConfirmation>();

/** Key order independent, so the bound-arguments check compares values. */
export function canonicalArgs(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalArgs).join(",")}]`;
  if (v && typeof v === "object")
    return `{${Object.keys(v)
      .sort()
      .map(
        (k) =>
          `${JSON.stringify(k)}:${canonicalArgs((v as Record<string, unknown>)[k])}`,
      )
      .join(",")}}`;
  return JSON.stringify(v ?? null);
}

const fresh = (p: PendingConfirmation, now: number) =>
  now - p.at < PENDING_TTL_MS;

export function notePending(
  p: Omit<PendingConfirmation, "at">,
  now = Date.now(),
): void {
  const key = canonicalArgs(p.args);
  for (const [id, q] of pending) {
    // The same call asked again replaces the older card instead of stacking.
    const same =
      q.ownerId === p.ownerId &&
      q.chatId === p.chatId &&
      q.tool === p.tool &&
      canonicalArgs(q.args) === key;
    if (same || !fresh(q, now)) pending.delete(id);
  }
  pending.set(p.id, { ...p, at: now });
}

export function listPending(
  ownerId: string,
  chatId: string | null,
  now = Date.now(),
): PendingConfirmation[] {
  return [...pending.values()].filter(
    (p) => p.ownerId === ownerId && p.chatId === chatId && fresh(p, now),
  );
}

/**
 * Remove and return the pending call, only if it is this owner's, fresh, and
 * exactly the tool and arguments the operator was shown. Synchronous, so two
 * presses racing cannot both get it.
 */
export function takePending(
  id: string,
  ownerId: string,
  tool: string,
  args: unknown,
  now = Date.now(),
): PendingConfirmation | "not_pending" | "args_mismatch" {
  const p = pending.get(id);
  if (!p || p.ownerId !== ownerId || !fresh(p, now)) return "not_pending";
  if (p.tool !== tool || canonicalArgs(p.args) !== canonicalArgs(args))
    return "args_mismatch";
  pending.delete(id);
  return p;
}

/** The operator's Cancelar: remove it if it is this owner's call of `tool`. */
export function dropPending(
  id: string,
  ownerId: string,
  tool: string,
): PendingConfirmation | null {
  const p = pending.get(id);
  if (!p || p.ownerId !== ownerId || p.tool !== tool) return null;
  pending.delete(id);
  return p;
}

/** Back on the list: the grant failed, so nothing ran. */
export function restorePending(p: PendingConfirmation): void {
  pending.set(p.id, p);
}

/** Tests only. */
export function clearPending(): void {
  pending.clear();
}

/**
 * SAFETY DECISION: these whole utterances, and nothing else, confirm or cancel
 * a pending arm/hand call by voice. They are matched against the operator's
 * own push-to-talk transcript after normalising case, accents and punctuation,
 * so «Sí, confirmo.» counts and «no sé si confirmo que vaya» does not. Adding a
 * phrase widens what moves the robot: keep them short and unmistakable.
 */
export const SPOKEN_PHRASES = {
  confirm: ["confirmo", "si confirmo", "confirmado", "si confirmado"],
  cancel: ["cancelar", "cancelo", "cancela", "no", "no confirmo"],
} as const;

export function normalizeUtterance(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

export type SpokenDecision =
  | { kind: "none" }
  | { kind: "ambiguous"; count: number }
  | { kind: "confirm" | "cancel"; pending: PendingConfirmation };

/**
 * What an operator's utterance does to the confirmations on their screen.
 * `shown` is the call the client displays: a fresh pending call the screen no
 * longer shows is never confirmed by voice. "none" sends the utterance to the
 * model as usual.
 */
export function decideSpoken(
  text: string,
  inScope: PendingConfirmation[],
  shown: string | undefined,
): SpokenDecision {
  const said = normalizeUtterance(text);
  const kind = (SPOKEN_PHRASES.confirm as readonly string[]).includes(said)
    ? "confirm"
    : (SPOKEN_PHRASES.cancel as readonly string[]).includes(said)
      ? "cancel"
      : null;
  if (!kind || !shown || inScope.length === 0) return { kind: "none" };
  if (inScope.length > 1) return { kind: "ambiguous", count: inScope.length };
  return inScope[0].id === shown
    ? { kind, pending: inScope[0] }
    : { kind: "none" };
}

const cut = (s: string, n = 600) => (s.length > n ? `${s.slice(0, n)}…` : s);

/** What the model reads after the operator's decision. It never says "retry". */
export function outcomeMessage(
  tool: string,
  outcome: { ok: true; result: unknown } | { ok: false; error: string },
): string {
  return outcome.ok
    ? `El operador confirmó y se ejecutó ${tool}. Resultado: ${cut(JSON.stringify(outcome.result ?? null))}. No lo vuelvas a llamar: seguí desde este resultado.`
    : `El operador confirmó ${tool}, pero falló: ${cut(outcome.error)}. No lo vuelvas a llamar sin decírselo al operador.`;
}

export const cancelMessage = (tool: string) =>
  `El operador canceló ${tool}: no se ejecutó. No lo vuelvas a llamar salvo que te lo pidan de nuevo.`;

/**
 * Where a legacy-voice-loop outcome goes (routes/voice.ts sets it): that
 * conversation has no client to relay the message.
 */
let loopFeedback: ((message: string, spoken: string) => void) | null = null;
export function onLoopOutcome(
  fn: ((message: string, spoken: string) => void) | null,
): void {
  loopFeedback = fn;
}
export function feedLoop(message: string, spoken: string): void {
  loopFeedback?.(message, spoken);
}
