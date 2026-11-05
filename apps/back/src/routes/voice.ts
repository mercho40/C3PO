/**
 * /voice/* — a spoken conversation session with the robot.
 *
 * `VoiceLoop` was written, tested and then connected to nothing: the only
 * reference to it anywhere in `apps/back` was its own test file. So everything
 * downstream worked in isolation — the bridge hears, the agent reasons, the
 * robot speaks — and no path existed from the first to the second. This is that
 * path, and it is the fourth time in this project a finished component sat
 * unwired while its absence read as "not built yet".
 *
 * A ROUTE AND NOT AN AUTOSTART, because `voice/loop.ts` says so in its own
 * header and the reason survives contact with the robot: a loop that reasons
 * about every overheard sentence is a privacy problem and a bill. Starting it
 * is an operator action, taken by a named person, and it stops on request.
 *
 * Guarded by `{ auth: true }`, and `.use(betterAuth)` here rather than relying
 * on the composition root — the rule in CLAUDE.md, and this module reads `user`.
 *
 * ONE LOOP PER PROCESS. The bridge event stream is non-consuming, so two loops
 * would both receive every utterance and the robot would answer everything
 * twice. The singleton is the whole concurrency story; `start()` on a running
 * loop is a no-op by construction.
 *
 * ONE ENGINE AT A TIME, CHOSEN PER SESSION. The operator picks `legacy` or
 * `openai-realtime` when starting; `VOICE_ENGINE` is only the default. Both
 * engines share one microphone and one speaker, so a start naming a different
 * engine than the running one is a 409, not a second session — the two would
 * answer the same sentence twice, in two voices.
 */

import { Elysia, t } from "elysia";
import { createIdGenerator } from "ai";

import { runAgentChat } from "#back/agent/runtime.js";
import { callTool } from "#back/bridge/client.js";
import { auditedCall } from "#back/bridge/audited.js";
import { deleteChat, ensureChat } from "#back/db/chats.js";
import {
  decideSpoken,
  listPending,
  onLoopOutcome,
  type PendingConfirmation,
} from "#back/agent/confirmations.js";
import { betterAuth } from "#back/lib/auth-plugin.js";
import { VoiceConversation } from "#back/voice/conversation.js";
import { bridgeVoiceEvents, bridgeVoiceStatus } from "#back/voice/events.js";
import { VoiceLoop } from "#back/voice/loop.js";
import { RealtimeVoiceSession } from "#back/voice/realtime.js";
import {
  MAX_AUDIO_BYTES,
  TranscribeError,
  transcribe,
} from "#back/voice/transcribe.js";
import {
  availableEngines,
  chooseEngine,
  defaultEngine,
  type VoiceEngine,
} from "#back/voice/engine.js";
import { cancelPending, confirmPending } from "./skills";

const newId = createIdGenerator();

/**
 * Run one heard utterance as the next turn of a spoken conversation.
 *
 * The generic agent streams text because that is what typed chat needs. Here the
 * host owns the final mile: it speaks each complete sentence as soon as it is
 * generated, serializes those calls, and retains bounded dialogue history for
 * follow-ups. `say` and `listen` are excluded from the model's voice-session
 * tools, so it cannot double-speak or start a competing listener.
 *
 * No chatId: a spoken session is intentionally separate from anybody's typed
 * conversation history. It lives only for this process/session and is cleared
 * whenever a new voice session starts.
 */
/**
 * Who started the legacy loop, for the audit trail. Its turns have no chat to
 * look an owner up from, so without this every robot command a spoken turn
 * dispatched was logged with nobody behind it. Set on a stopped -> running
 * start, cleared on stop.
 */
let legacyStartedBy: string | null = null;

const conversation = new VoiceConversation({
  // The whole result, not just its text: the turn also waits on toolsSettled.
  complete: (messages, signal) =>
    runAgentChat(messages, {
      mode: "voice",
      userId: legacyStartedBy,
      abortSignal: signal,
    }),
  speak: async (text) => {
    await auditedCall(
      "say",
      { text, language: "spanish", wait_for_completion: true },
      { userId: legacyStartedBy, source: "voice" },
    );
  },
  newId,
});

// A Confirmar/Cancelar of a call this loop asked for: its next turn reads the
// outcome, and the robot says it in one line (no model, so it cannot overlap a
// turn the loop is running).
onLoopOutcome((message, spoken) => {
  conversation.remember(message);
  void auditedCall(
    "say",
    { text: spoken, language: "spanish", wait_for_completion: false },
    { userId: legacyStartedBy, source: "voice" },
  ).catch(() => {});
});

/** A refused call waiting for this operator, as the /voice page shows it. */
const publicPending = (p: PendingConfirmation) => ({
  id: p.id,
  tool: p.tool,
  args: p.args,
  askedAt: new Date(p.at).toISOString(),
});

async function runAgentOnUtterance(
  utterance: string,
  signal?: AbortSignal,
): Promise<void> {
  const metrics = await conversation.turn(utterance, signal);
  console.log("[voice] turn complete", metrics);
}

let loop: VoiceLoop | null = null;
const realtime = new RealtimeVoiceSession();

/**
 * The engine status describes: whichever is running, else whichever ran last.
 * "Last", not "default": a realtime session that failed reports its error with
 * `running: false`, and falling back to the default would hide it.
 */
let lastEngine: VoiceEngine = defaultEngine();
/**
 * Set while a realtime start is between its checks and `realtime.start()`:
 * creating the chat row is awaited first, and a legacy start landing in that
 * window would otherwise see nothing running and open the mic twice.
 */
let realtimeStarting = false;

function runningEngine(): VoiceEngine | null {
  if (realtimeStarting || realtime.snapshot().running) return "openai-realtime";
  if (loop?.snapshot().running) return "legacy";
  return null;
}

const engineChoice = {
  defaultEngine: defaultEngine(),
  availableEngines: availableEngines(),
};

/**
 * The status an operator sees. `userId` decides ownership: a realtime session
 * belongs to the operator who started it, and a second operator's console must
 * be able to say "in use by someone else" rather than offering an End button
 * that can only 409, or a transcript link to a chat that is not theirs.
 */
function voiceStatus(userId?: string) {
  if ((runningEngine() ?? lastEngine) === "openai-realtime") {
    const state = realtime.snapshot();
    const { ownerId, chatId, ...publicState } = state;
    const ownedByYou = state.running ? ownerId === userId : null;
    return {
      ...publicState,
      engine: "openai-realtime" as const,
      ...engineChoice,
      // Only the owner gets the transcript id — it is their chat, and it 404s
      // for anyone else.
      chatId: ownedByYou ? chatId : null,
      ownedByYou,
      confirmations:
        ownedByYou && userId && chatId
          ? listPending(userId, chatId).map(publicPending)
          : [],
      // Compatibility fields keep the existing dashboard readable while the
      // richer Realtime state is additive.
      utterancesHeard: state.utterancesHeard,
      agentRuns: state.agentRuns,
      // Null, not 0: in a realtime session the bridge acts on the stop phrase
      // itself and apps/back never sees it, so "0" would read as none heard.
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
      ? listPending(userId, null)
          .filter((p) => p.loop)
          .map(publicPending)
      : [],
    conversation: conversation.state(),
  };
}

function getLoop(): VoiceLoop {
  if (loop === null) {
    loop = new VoiceLoop({
      // Audited, except the `listen` poll: it commands nothing and runs several
      // times a second. The spoken "stop" goes out through here, and is
      // exactly the command an audit trail must not miss.
      callTool: (name, args) =>
        name === "listen"
          ? callTool(name, args)
          : auditedCall(name, args, {
              userId: legacyStartedBy,
              source: "voice",
            }),
      runAgent: runAgentOnUtterance,
      events: bridgeVoiceEvents,
      inputStatus: bridgeVoiceStatus,
      log: (event, detail) => console.log(`[voice] ${event}`, detail ?? ""),
    });
  }
  return loop;
}

export const voiceRoutes = new Elysia({ prefix: "/voice" })
  .use(betterAuth)
  .post(
    "/start",
    async ({ user, session, body, status }) => {
      // 1.4: driving the robot through a model is an admin's job, like /invoke.
      if (user.role !== "admin")
        return status(403, { error: "admin_required" });
      const decision = chooseEngine({
        requested: body?.engine,
        running: runningEngine(),
        fallback: engineChoice.defaultEngine,
        available: engineChoice.availableEngines,
      });
      console.log(
        `[voice] conversation start (${body?.engine ?? "default"}) requested by ${user.id}`,
      );
      if (!decision.ok)
        return status(decision.status, {
          ...voiceStatus(user.id),
          error:
            decision.error === "engine_unavailable"
              ? "OPENAI_API_KEY is not configured"
              : decision.error,
        });
      const engine = decision.engine;
      lastEngine = engine;
      if (engine === "openai-realtime") {
        const active = realtime.snapshot();
        if (active.running && active.ownerId !== user.id)
          return status(409, voiceStatus(user.id));
        if (!active.running && !realtimeStarting) {
          realtimeStarting = true;
          try {
            const chatId = crypto.randomUUID();
            const owned = await ensureChat({
              id: chatId,
              userId: user.id,
              organizationId: session.activeOrganizationId ?? null,
              title: `Conversación de voz · ${new Date().toLocaleString("es-AR")}`,
              channel: "voice",
            });
            if (!owned) return status(409, { error: "chat_id_conflict" });
            try {
              await realtime.start({ chatId, ownerId: user.id });
            } catch (error) {
              await deleteChat(chatId, user.id).catch(() => false);
              return status(503, {
                error: error instanceof Error ? error.message : String(error),
                ...voiceStatus(user.id),
              });
            }
          } finally {
            realtimeStarting = false;
          }
        }
        return voiceStatus(user.id);
      }

      const l = getLoop();
      // A stopped -> running transition begins a fresh conversation. Repeated
      // start requests are idempotent and must not erase an active dialogue.
      if (!l.snapshot().running) {
        conversation.reset();
        legacyStartedBy = user.id;
      }
      l.start();
      return voiceStatus(user.id);
    },
    {
      auth: true,
      body: t.Optional(
        t.Object({
          engine: t.Optional(
            t.Union([t.Literal("legacy"), t.Literal("openai-realtime")]),
          ),
        }),
      ),
      detail: {
        summary:
          "Start a spoken conversation on the chosen engine (default: VOICE_ENGINE): the agent answers and uses robot tools only when requested.",
        tags: ["voice"],
      },
    },
  )
  .post(
    "/stop",
    async ({ user, status }) => {
      console.log(`[voice] stop requested by ${user.id}`);
      if ((runningEngine() ?? lastEngine) === "openai-realtime") {
        const active = realtime.snapshot();
        if (active.running && active.ownerId !== user.id)
          return status(409, voiceStatus(user.id));
        await realtime.stop();
      } else {
        await getLoop().stop();
        legacyStartedBy = null;
      }
      return voiceStatus(user.id);
    },
    {
      auth: true,
      detail: {
        summary:
          "End the spoken conversation. The robot keeps listening, but no turns are processed.",
        tags: ["voice"],
      },
    },
  )
  .post(
    "/transcribe",
    async ({ request, status, user, query, server }) => {
      // Admin only too: it spends the shared transcription key, and its text drives /agent.
      if (user.role !== "admin")
        return status(403, { error: "admin_required" });
      const declared = Number(request.headers.get("content-length") ?? 0);
      if (declared > MAX_AUDIO_BYTES) {
        return status(413, { error: "audio_too_long" });
      }
      try {
        const text = await transcribe(
          await request.arrayBuffer(),
          request.headers.get("content-type") ?? "audio/wav",
        );
        // The admin's OWN push-to-talk, decided here and never by the model:
        // «confirmo» runs the one refused call this client shows (`pending`,
        // in chat `chatId`) exactly as the Confirmar button would. Anything
        // else, or no call on screen, goes to the agent as usual.
        const decision = decideSpoken(
          text,
          query.chatId ? listPending(user.id, query.chatId) : [],
          query.pending,
        );
        if (decision.kind === "none") return { text };
        server?.timeout(request, 0); // the confirmed call may outlast Bun's 10 s idle cut
        if (decision.kind === "ambiguous")
          return {
            text,
            confirmation: { action: "ambiguous", count: decision.count },
          };
        const p = decision.pending;
        if (decision.kind === "cancel") {
          const r = cancelPending(p.tool, p.id, user);
          return {
            text,
            confirmation: { action: "cancelled", tool: p.tool, ...r.body },
          };
        }
        const r = await confirmPending(p.tool, p.id, p.args, user, "voice");
        return {
          text,
          confirmation: {
            action: "confirmed",
            tool: p.tool,
            args: p.args,
            httpStatus: r.status,
            ...r.body,
          },
        };
      } catch (error) {
        if (error instanceof TranscribeError) {
          return status(error.status, { error: error.message });
        }
        throw error;
      }
    },
    {
      auth: true,
      parse: "none",
      // The headset's chat and the refused call it shows: spoken «confirmo»
      // applies to that call only.
      query: t.Object({
        chatId: t.Optional(t.String({ maxLength: 64 })),
        pending: t.Optional(t.String({ maxLength: 128 })),
      }),
      detail: {
        summary:
          "Speech to text for the headset's push-to-talk (WAV in, {text} out).",
        tags: ["voice"],
      },
    },
  )
  .get("/status", ({ user }) => voiceStatus(user.id), {
    auth: true,
    detail: {
      summary:
        "Whether the loop is running, and what it has heard and done so far.",
      tags: ["voice"],
    },
  });
