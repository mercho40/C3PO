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
 */

import { Elysia } from "elysia";
import { createIdGenerator } from "ai";

import { runAgentChat } from "#back/agent/runtime.js";
import { callTool } from "#back/bridge/client.js";
import { auditedCall } from "#back/bridge/audited.js";
import { deleteChat, ensureChat } from "#back/db/chats.js";
import { betterAuth } from "#back/lib/auth-plugin.js";
import { VoiceConversation } from "#back/voice/conversation.js";
import { bridgeVoiceEvents, bridgeVoiceStatus } from "#back/voice/events.js";
import { VoiceLoop } from "#back/voice/loop.js";
import { RealtimeVoiceSession } from "#back/voice/realtime.js";

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
  complete: async (messages) => {
    const result = await runAgentChat(messages, {
      mode: "voice",
      userId: legacyStartedBy,
    });
    return result.textStream;
  },
  speak: async (text) => {
    await auditedCall(
      "say",
      { text, language: "spanish", wait_for_completion: true },
      { userId: legacyStartedBy, source: "voice" },
    );
  },
  newId,
});

async function runAgentOnUtterance(utterance: string): Promise<void> {
  const metrics = await conversation.turn(utterance);
  console.log("[voice] turn complete", metrics);
}

let loop: VoiceLoop | null = null;
const realtime = new RealtimeVoiceSession();
const useRealtime = () => process.env.VOICE_ENGINE === "realtime";

/**
 * The status an operator sees. `userId` decides ownership: a realtime session
 * belongs to the operator who started it, and a second operator's console must
 * be able to say "in use by someone else" rather than offering an End button
 * that can only 409, or a transcript link to a chat that is not theirs.
 */
function voiceStatus(userId?: string) {
  if (useRealtime()) {
    const state = realtime.snapshot();
    const { ownerId, chatId, ...publicState } = state;
    const ownedByYou = state.running ? ownerId === userId : null;
    return {
      ...publicState,
      engine: "openai-realtime" as const,
      // Only the owner gets the transcript id — it is their chat, and it 404s
      // for anyone else.
      chatId: ownedByYou ? chatId : null,
      ownedByYou,
      // Compatibility fields keep the existing dashboard readable while the
      // richer Realtime state is additive.
      utterancesHeard: state.utterancesHeard,
      agentRuns: state.agentRuns,
      // Null, not 0: the realtime session does not act on spoken stop phrases
      // at all (only the legacy loop does), so a counter reading "0" would
      // imply a guard that is not there.
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
    chatId: null,
    // The legacy loop is process-wide and unowned: anyone signed in may stop it.
    ownedByYou: null,
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
    async ({ user, session, status }) => {
      console.log(`[voice] conversation start requested by ${user.id}`);
      if (useRealtime()) {
        const active = realtime.snapshot();
        if (active.running && active.ownerId !== user.id)
          return status(409, voiceStatus(user.id));
        if (!active.running) {
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
      detail: {
        summary:
          "Start a spoken conversation: the agent answers and uses robot tools only when requested.",
        tags: ["voice"],
      },
    },
  )
  .post(
    "/stop",
    async ({ user, status }) => {
      console.log(`[voice] stop requested by ${user.id}`);
      if (useRealtime()) {
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
  .get("/status", ({ user }) => voiceStatus(user.id), {
    auth: true,
    detail: {
      summary:
        "Whether the loop is running, and what it has heard and done so far.",
      tags: ["voice"],
    },
  });
