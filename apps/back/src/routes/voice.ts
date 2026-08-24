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

import { runAgentChat } from "@back/agent/runtime";
import { callTool } from "@back/bridge/client";
import { betterAuth } from "@back/lib/auth-plugin";
import { VoiceConversation } from "@back/voice/conversation";
import { bridgeVoiceEvents, bridgeVoiceStatus } from "@back/voice/events";
import { VoiceLoop } from "@back/voice/loop";

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
const conversation = new VoiceConversation({
  complete: async (messages) => {
    const result = await runAgentChat(messages, { mode: "voice" });
    return result.textStream;
  },
  speak: async (text) => {
    await callTool("say", {
      text,
      language: "spanish",
      wait_for_completion: true,
    });
  },
  newId,
});

async function runAgentOnUtterance(utterance: string): Promise<void> {
  const metrics = await conversation.turn(utterance);
  console.log("[voice] turn complete", metrics);
}

let loop: VoiceLoop | null = null;

function voiceStatus() {
  return { ...getLoop().snapshot(), conversation: conversation.state() };
}

function getLoop(): VoiceLoop {
  if (loop === null) {
    loop = new VoiceLoop({
      callTool: (name, args) => callTool(name, args),
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
    ({ user }) => {
      const l = getLoop();
      // A stopped -> running transition begins a fresh conversation. Repeated
      // start requests are idempotent and must not erase an active dialogue.
      if (!l.snapshot().running) conversation.reset();
      console.log(`[voice] conversation start requested by ${user.id}`);
      l.start();
      return voiceStatus();
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
    async ({ user }) => {
      const l = getLoop();
      console.log(`[voice] stop requested by ${user.id}`);
      await l.stop();
      return voiceStatus();
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
  .get("/status", () => voiceStatus(), {
    auth: true,
    detail: {
      summary:
        "Whether the loop is running, and what it has heard and done so far.",
      tags: ["voice"],
    },
  });
