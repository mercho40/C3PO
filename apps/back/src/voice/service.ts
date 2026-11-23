/**
 * The voice composition: the legacy loop, the realtime session, the
 * listen-after-greet window, and the one-engine-at-a-time rule between them.
 *
 * It used to be routes/voice.ts's import-time side effects: importing the
 * route module built the conversation, the realtime session and the listen
 * window, and installed the confirmation hooks, so the first module to import
 * it (index.ts, or face.ts for the window) wired the voice stack. Now
 * `createVoiceService` builds it from its dependencies, once, in createApp,
 * and the routes (/voice, /face) are handed what they need.
 *
 * ONE LOOP PER PROCESS. The bridge event stream is non-consuming, so two loops
 * would both receive every utterance and the robot would answer everything
 * twice. One service is the whole concurrency story; `start()` on a running
 * loop is a no-op by construction.
 *
 * ONE ENGINE AT A TIME, CHOSEN PER SESSION. The operator picks `legacy` or
 * `openai-realtime` when starting; `VOICE_ENGINE` is only the default. Both
 * engines share one microphone and one speaker, so a start naming a different
 * engine than the running one is a 409, not a second session — the two would
 * answer the same sentence twice, in two voices.
 */

import { createIdGenerator } from "ai";

import type { ConfirmService } from "#back/agent/confirm-service.js";
import {
  decideSpoken,
  type Confirmations,
  type PendingConfirmation,
  type SpokenDecision,
} from "#back/agent/confirmations.js";
import type { AgentRuntime } from "#back/agent/runtime.js";
import { readStoppedAt } from "#back/agent/stop-watch.js";
import type { Turns } from "#back/agent/turns.js";
import type { BridgeHttp } from "#back/bridge/http.js";
import type { BridgeMcp } from "#back/bridge/client.js";
import type { Config } from "#back/config.js";
import type { ChatStore } from "#back/db/chats.js";
import type { Dispatcher } from "#back/skills/dispatch.js";
import type { InvokeUser } from "#back/skills/invoke.js";
import { VoiceConversation } from "./conversation";
import { chooseEngine, type VoiceEngine } from "./engine";
import { bridgeVoice } from "./events";
import { ListenWindow } from "./listen-window";
import { VoiceLoop } from "./loop";
import { RealtimeVoiceSession, type RealtimeDeps } from "./realtime";
import { transcribeTimeoutMs, type Transcribe } from "./transcribe";

const newId = createIdGenerator();

/** A refused call waiting for this operator, as the /voice page shows it. */
const publicPending = (p: PendingConfirmation) => ({
  id: p.id,
  tool: p.tool,
  args: p.args,
  askedAt: new Date(p.at).toISOString(),
});

export type VoiceServiceDeps = {
  config: Config["voice"];
  realtime: RealtimeDeps;
  agent: Pick<AgentRuntime, "runAgentChat">;
  bridge: Pick<BridgeMcp, "callTool">;
  dispatch: Pick<Dispatcher, "auditedCall">;
  http: Pick<BridgeHttp, "request" | "faceSeen" | "safetyStatus">;
  chats: Pick<ChatStore, "ensureChat" | "deleteChat">;
  confirmations: Pick<Confirmations, "list" | "subscribe">;
  confirm: Pick<ConfirmService, "confirmPending" | "cancelPending">;
  turns: Pick<Turns, "abortAll" | "beginTurn" | "voiceLoopHeld">;
  transcribe: Transcribe;
};

export type StartReply = { status: 200 | 409 | 503; body: unknown };

export function createVoiceService(deps: VoiceServiceDeps) {
  const voice = bridgeVoice(deps.http);

  /**
   * Who started the legacy loop, for the audit trail. Its turns have no chat
   * to look an owner up from, so without this every robot command a spoken
   * turn dispatched was logged with nobody behind it. Set on a stopped ->
   * running start, cleared on stop.
   */
  let legacyStartedBy: string | null = null;

  /**
   * Every legacy-loop `say`, one at a time: each takes a new stream on the
   * bridge and barges in on the one playing, so the Confirmar line cut into a
   * turn's sentence (2026-11-13 13:56:59). A failed say does not block the next.
   */
  let speech: Promise<unknown> = Promise.resolve();
  function speakInOrder(text: string): Promise<unknown> {
    speech = speech
      .catch(() => {})
      .then(() =>
        deps.dispatch.auditedCall(
          "say",
          { text, language: "spanish", wait_for_completion: true },
          { userId: legacyStartedBy, source: "voice" },
        ),
      );
    return speech;
  }

  /**
   * Run one heard utterance as the next turn of a spoken conversation.
   *
   * The generic agent streams text because that is what typed chat needs.
   * Here the host owns the final mile: it speaks each complete sentence as
   * soon as it is generated, serializes those calls, and retains bounded
   * dialogue history for follow-ups. `say` and `listen` are excluded from the
   * model's voice-session tools, so it cannot double-speak or start a
   * competing listener.
   *
   * No chatId: a spoken session is intentionally separate from anybody's
   * typed conversation history. It lives only for this process/session and is
   * cleared whenever a new voice session starts.
   */
  const conversation = new VoiceConversation({
    // The whole result, not just its text: the turn also waits on toolsSettled.
    complete: (messages, signal) =>
      deps.agent.runAgentChat(messages, {
        mode: "voice",
        userId: legacyStartedBy,
        abortSignal: signal,
      }),
    speak: async (text) => {
      await speakInOrder(text);
    },
    newId,
  });

  async function runAgentOnUtterance(
    utterance: string,
    signal?: AbortSignal,
  ): Promise<void> {
    const metrics = await conversation.turn(utterance, signal);
    console.log("[voice] turn complete", metrics);
  }

  const realtime = new RealtimeVoiceSession(deps.realtime);

  // A Confirmar/Cancelar of a call this service's engines asked for. The
  // legacy loop's next turn reads the outcome, and the robot says it in one
  // line, queued behind any sentence the loop is saying. The realtime session
  // hears its chat's (it checks the chat is still its own).
  deps.confirmations.subscribe((outcome) => {
    if (outcome.kind === "loop") {
      conversation.remember(outcome.message);
      void speakInOrder(outcome.spoken).catch(() => {});
    } else realtime.informOutcome(outcome.chatId, outcome.message);
  });

  let loop: VoiceLoop | null = null;

  /**
   * The engine status describes: whichever is running, else whichever ran
   * last. "Last", not "default": a realtime session that failed reports its
   * error with `running: false`, and falling back to the default would hide it.
   */
  let lastEngine: VoiceEngine = deps.config.defaultEngine;
  /**
   * Set while a realtime start is between its checks and `realtime.start()`:
   * creating the chat row is awaited first, and a legacy start landing in
   * that window would otherwise see nothing running and open the mic twice.
   */
  let realtimeStarting = false;

  function runningEngine(): VoiceEngine | null {
    if (realtimeStarting || realtime.snapshot().running)
      return "openai-realtime";
    if (loop?.snapshot().running) return "legacy";
    return null;
  }

  const engineChoice = {
    defaultEngine: deps.config.defaultEngine,
    availableEngines: deps.config.availableEngines,
  };

  /**
   * The status an operator sees. `userId` decides ownership: a realtime
   * session belongs to the operator who started it, and a second operator's
   * console must be able to say "in use by someone else" rather than offering
   * an End button that can only 409, or a transcript link to a chat that is
   * not theirs.
   */
  function status(userId?: string) {
    if ((runningEngine() ?? lastEngine) === "openai-realtime") {
      const state = realtime.snapshot();
      const { ownerId, chatId, ...publicState } = state;
      const ownedByYou = state.running ? ownerId === userId : null;
      return {
        ...publicState,
        engine: "openai-realtime" as const,
        ...engineChoice,
        // Only the owner gets the transcript id — it is their chat, and it
        // 404s for anyone else.
        chatId: ownedByYou ? chatId : null,
        ownedByYou,
        confirmations:
          ownedByYou && userId && chatId
            ? deps.confirmations.list(userId, chatId).map(publicPending)
            : [],
        // Compatibility fields keep the existing dashboard readable while the
        // richer Realtime state is additive.
        utterancesHeard: state.utterancesHeard,
        agentRuns: state.agentRuns,
        // Null, not 0: in a realtime session the bridge acts on the stop
        // phrase itself and apps/back never sees it, so "0" would read as none
        // heard.
        stopsTriggered: null,
        micEverOpen: state.micEverOpen,
        alwaysListening: null,
        conversation: {
          phase: state.phase === "speaking" ? "speaking" : "idle",
          lastTurn: null,
        },
      };
    }
    return {
      ...getLoop().snapshot(),
      engine: "legacy" as const,
      ...engineChoice,
      chatId: null,
      // The legacy loop is process-wide and unowned: anyone signed in may stop it.
      ownedByYou: null,
      // Its refused calls wait for whoever started it (their turns asked).
      confirmations: userId
        ? deps.confirmations
            .list(userId, null)
            .filter((p) => p.loop)
            .map(publicPending)
        : [],
      conversation: conversation.state(),
    };
  }

  function getLoop(): VoiceLoop {
    if (loop === null) {
      loop = new VoiceLoop({
        turns: deps.turns,
        // Audited, except the `listen` poll: it commands nothing and runs
        // several times a second. The spoken "stop" goes out through here, and
        // is exactly the command an audit trail must not miss.
        callTool: (name, args) =>
          name === "listen"
            ? deps.bridge.callTool(name, args)
            : deps.dispatch.auditedCall(name, args, {
                userId: legacyStartedBy,
                source: "voice",
              }),
        runAgent: runAgentOnUtterance,
        events: voice.events,
        // Bridge C3PO_STT=back: the robot mic's utterances, transcribed by ia-tic.
        transcribeAudio: async (audioId) => {
          const wav = await voice.utteranceAudio(
            audioId,
            AbortSignal.timeout(2_000),
          );
          return deps.transcribe(
            wav,
            "audio/wav",
            transcribeTimeoutMs(wav.byteLength),
          );
        },
        inputStatus: voice.status,
        log: (event, detail) => console.log(`[voice] ${event}`, detail ?? ""),
      });
    }
    return loop;
  }

  /** Start the legacy loop for `owner`, as /voice/start does on stopped -> running. */
  function startLegacy(owner: string): void {
    const l = getLoop();
    if (!l.snapshot().running) {
      conversation.reset();
      legacyStartedBy = owner;
    }
    lastEngine = "legacy";
    l.start();
  }

  async function stopLegacy(): Promise<void> {
    await getLoop().stop();
    legacyStartedBy = null;
  }

  /** The bridge's last enrolled greeting (/face/seen `greet`), or null. */
  async function readGreet() {
    try {
      const res = await deps.http.faceSeen(1000);
      if (!res.ok || res.body === undefined) return null;
      const g = (res.body as { greet?: Record<string, unknown> }).greet;
      if (!g || typeof g.seq !== "number") return null;
      return {
        seq: g.seq,
        name: typeof g.name === "string" ? g.name : null,
        listen: g.listen_after_greet === true,
      };
    } catch {
      return null;
    }
  }

  /** «Escuchar después de saludar» (voice/listen-window.ts). */
  const listenWindow = new ListenWindow(
    {
      readGreet,
      readStoppedAt: () => readStoppedAt(deps.http),
      loopRunning: () => runningEngine() !== null,
      startLoop: startLegacy,
      stopLoop: stopLegacy,
      activity: () => {
        const s = getLoop().snapshot();
        return s.utterancesHeard + s.agentRuns;
      },
      stops: () => getLoop().snapshot().stopsTriggered,
      busy: () => conversation.state().phase !== "idle",
      log: (event, detail) => console.log(`[voice] ${event}`, detail ?? ""),
    },
    deps.config.listenAfterGreetMs,
  );

  /**
   * Once a second, for the process's life (index.ts). Ticks never overlap.
   * Returns the stop.
   */
  function startListenAfterGreet(): () => void {
    let stopped = false;
    const run = async () => {
      if (stopped) return;
      await listenWindow.tick().catch((e) => {
        console.error("[voice] listen_window.tick_failed", e);
      });
      if (!stopped) setTimeout(run, 1000).unref?.();
    };
    void run();
    return () => {
      stopped = true;
    };
  }

  /** POST /voice/start's decision, for an admin. */
  async function start(
    user: { id: string },
    organizationId: string | null,
    requested: VoiceEngine | undefined,
  ): Promise<StartReply> {
    const decision = chooseEngine({
      requested,
      running: runningEngine(),
      fallback: engineChoice.defaultEngine,
      available: engineChoice.availableEngines,
    });
    console.log(
      `[voice] conversation start (${requested ?? "default"}) requested by ${user.id}`,
    );
    if (!decision.ok)
      return {
        status: decision.status,
        body: {
          ...status(user.id),
          error:
            decision.error === "engine_unavailable"
              ? "OPENAI_API_KEY is not configured"
              : decision.error,
        },
      };
    const engine = decision.engine;
    lastEngine = engine;
    if (engine === "openai-realtime") {
      const active = realtime.snapshot();
      if (active.running && active.ownerId !== user.id)
        return { status: 409, body: status(user.id) };
      if (!active.running && !realtimeStarting) {
        realtimeStarting = true;
        try {
          const chatId = crypto.randomUUID();
          const owned = await deps.chats.ensureChat({
            id: chatId,
            userId: user.id,
            organizationId,
            title: `Conversación de voz · ${new Date().toLocaleString("es-AR")}`,
            channel: "voice",
          });
          if (!owned)
            return { status: 409, body: { error: "chat_id_conflict" } };
          try {
            await realtime.start({ chatId, ownerId: user.id });
          } catch (error) {
            await deps.chats.deleteChat(chatId, user.id).catch(() => false);
            return {
              status: 503,
              body: {
                error: error instanceof Error ? error.message : String(error),
                ...status(user.id),
              },
            };
          }
        } finally {
          realtimeStarting = false;
        }
      }
      return { status: 200, body: status(user.id) };
    }

    // A stopped -> running transition begins a fresh conversation. Repeated
    // start requests are idempotent and must not erase an active dialogue.
    // An admin's own start takes over a listen-after-greet window's loop:
    // the window must not stop it later, and its turns are this admin's.
    if (listenWindow.isOpen()) {
      listenWindow.release();
      legacyStartedBy = user.id;
    }
    startLegacy(user.id);
    return { status: 200, body: status(user.id) };
  }

  /** POST /voice/stop. 409 for someone else's realtime session. */
  async function stop(user: { id: string }): Promise<StartReply> {
    console.log(`[voice] stop requested by ${user.id}`);
    if ((runningEngine() ?? lastEngine) === "openai-realtime") {
      const active = realtime.snapshot();
      if (active.running && active.ownerId !== user.id)
        return { status: 409, body: status(user.id) };
      await realtime.stop();
    } else {
      await stopLegacy();
    }
    return { status: 200, body: status(user.id) };
  }

  /**
   * The admin's OWN push-to-talk, decided here and never by the model:
   * «confirmo» runs the one refused call this client shows (`pending`, in
   * chat `chatId`) exactly as the Confirmar button would. Anything else, or no
   * call on screen, is "none": it goes to the agent as usual.
   */
  function decideSpokenFor(
    text: string,
    user: { id: string },
    shown: { chatId?: string; pending?: string },
  ): SpokenDecision {
    return decideSpoken(
      text,
      shown.chatId ? deps.confirmations.list(user.id, shown.chatId) : [],
      shown.pending,
    );
  }

  /** Carry out a decision that is not "none": what the headset shows. */
  async function applySpoken(
    decision: Exclude<SpokenDecision, { kind: "none" }>,
    user: InvokeUser,
  ): Promise<Record<string, unknown>> {
    if (decision.kind === "ambiguous")
      return { action: "ambiguous", count: decision.count };
    const p = decision.pending;
    if (decision.kind === "cancel") {
      const r = deps.confirm.cancelPending(p.tool, p.id, user);
      return { action: "cancelled", tool: p.tool, ...r.body };
    }
    const r = await deps.confirm.confirmPending(
      p.tool,
      p.id,
      p.args,
      user,
      "voice",
    );
    return {
      action: "confirmed",
      tool: p.tool,
      args: p.args,
      httpStatus: r.status,
      ...r.body,
    };
  }

  return {
    start,
    stop,
    status,
    decideSpoken: decideSpokenFor,
    applySpoken,
    transcribe: deps.transcribe,
    listenWindow,
    startListenAfterGreet,
  };
}

export type VoiceService = ReturnType<typeof createVoiceService>;
