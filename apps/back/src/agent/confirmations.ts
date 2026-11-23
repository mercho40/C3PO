/**
 * Calls the bridge refused for want of an operator's confirmation, waiting for
 * one.
 *
 * A refused T2 / high-danger-T1 call is NOTED here by the agent's tool (and the
 * realtime voice session). It is never confirmed from here: only the admin
 * routes `POST /skills/:name/confirm` (a button) and `POST /voice/transcribe`
 * (the admin's own push-to-talk) take one, and they then grant AND run that
 * exact call themselves (agent/confirm-service.ts `confirmPending`). So the
 * model never retries after a confirmation, it is told the result.
 *
 * In memory on purpose: a back restart forgets every pending call, and the
 * operator asks again. Nothing here can grant anything; the bridge's own
 * argument-bound 30 s grant is still the gate.
 *
 * One store per process (`createConfirmations`, built by createApp): the
 * agent's tools, the realtime session, the confirm service, «Caras», PARAR
 * (turns.ts) and «Reanudar» all hold the same instance.
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

/**
 * Where an outcome is fed: `loop` to the legacy voice loop (its next turn
 * reads `message`, the robot says `spoken`), `chat` to whatever listens for
 * that chat (the realtime session).
 */
export type ConfirmationOutcome =
  | { kind: "loop"; message: string; spoken: string }
  | { kind: "chat"; chatId: string; message: string };

export function createConfirmations() {
  // Keyed by owner AND call id: the id is the model's, and two admins' turns can
  // mint the same one; keyed by id alone, the second card replaced the first.
  const pending = new Map<string, PendingConfirmation>();
  const keyOf = (ownerId: string, id: string) => JSON.stringify([ownerId, id]);
  // Bumped by every clear(); a taken card remembers the epoch it was taken
  // in, so a grant that fails after a PARAR cannot put it back.
  let stopEpoch = 0;
  const takenIn = new WeakMap<PendingConfirmation, number>();

  const fresh = (p: PendingConfirmation, now: number) =>
    now - p.at < PENDING_TTL_MS;

  function note(p: Omit<PendingConfirmation, "at">, now = Date.now()): void {
    const key = canonicalArgs(p.args);
    for (const [k, q] of pending) {
      // The same call asked again replaces the older card instead of stacking.
      const same =
        q.ownerId === p.ownerId &&
        q.chatId === p.chatId &&
        q.tool === p.tool &&
        canonicalArgs(q.args) === key;
      if (same || !fresh(q, now)) pending.delete(k);
    }
    pending.set(keyOf(p.ownerId, p.id), { ...p, at: now });
  }

  function list(
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
  function take(
    id: string,
    ownerId: string,
    tool: string,
    args: unknown,
    now = Date.now(),
  ): PendingConfirmation | "not_pending" | "args_mismatch" {
    const p = peek(id, ownerId, tool, args, now);
    if (typeof p === "string") return p;
    pending.delete(keyOf(ownerId, id));
    takenIn.set(p, stopEpoch);
    return p;
  }

  /** `take`'s checks without taking it: the face check runs before the call is taken. */
  function peek(
    id: string,
    ownerId: string,
    tool: string,
    args: unknown,
    now = Date.now(),
  ): PendingConfirmation | "not_pending" | "args_mismatch" {
    const p = pending.get(keyOf(ownerId, id));
    if (!p || !fresh(p, now)) return "not_pending";
    if (p.tool !== tool || canonicalArgs(p.args) !== canonicalArgs(args))
      return "args_mismatch";
    return p;
  }

  /** The operator's Cancelar: remove it if it is this owner's call of `tool`. */
  function drop(
    id: string,
    ownerId: string,
    tool: string,
  ): PendingConfirmation | null {
    const p = pending.get(keyOf(ownerId, id));
    if (!p || p.tool !== tool) return null;
    pending.delete(keyOf(ownerId, id));
    return p;
  }

  /**
   * Back on the list: the grant failed, so nothing ran. Unless a PARAR cleared
   * the cards while it was in flight: the stop wins, the card stays gone.
   */
  function restore(p: PendingConfirmation): void {
    if ((takenIn.get(p) ?? stopEpoch) !== stopEpoch) return;
    pending.set(keyOf(p.ownerId, p.id), p);
  }

  /** Every card, everyone's: PARAR and «Reanudar» (turns.ts, routes/safety.ts). */
  function clear(): void {
    stopEpoch++;
    pending.clear();
  }

  // Where a confirmed or cancelled call's outcome goes besides the client
  // that pressed the button: the legacy voice loop that asked it (it has no
  // client to relay the message) and the realtime session of its chat. A Set,
  // not a slot: each voice engine subscribes for itself, and a test that
  // subscribes does not replace the service's own listener.
  const listeners = new Set<(outcome: ConfirmationOutcome) => void>();

  /** Hear every outcome. Returns the unsubscribe. */
  function subscribe(fn: (outcome: ConfirmationOutcome) => void): () => void {
    listeners.add(fn);
    return () => void listeners.delete(fn);
  }

  function publish(outcome: ConfirmationOutcome): void {
    for (const fn of listeners) fn(outcome);
  }

  return { note, list, take, peek, drop, restore, clear, subscribe, publish };
}

export type Confirmations = ReturnType<typeof createConfirmations>;

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
