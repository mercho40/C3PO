/**
 * /voice/* — switching the voice loop on, which is what makes the robot answer.
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
 * ONE LOOP PER PROCESS. Two loops would poll the same non-consuming telemetry
 * and then both call `poll()`, so each would see half the utterances and the
 * robot would answer every other sentence. The singleton is the whole
 * concurrency story; `start()` on a running loop is a no-op by construction.
 */

import { Elysia } from "elysia";
import { createIdGenerator, type UIMessage } from "ai";

import { runAgentChat } from "@back/agent/runtime";
import { callTool } from "@back/bridge/client";
import { betterAuth } from "@back/lib/auth-plugin";
import { VoiceLoop } from "@back/voice/loop";

const newId = createIdGenerator();

/**
 * Run one heard utterance through the agent, to completion.
 *
 * `runAgentChat` returns a stream because its other caller is an HTTP response.
 * Nobody is reading this one, and an unread stream never advances — so the tool
 * calls the agent decides on would never execute and the robot would sit
 * silent having "handled" the sentence. `consumeStream()` is what makes the
 * turn actually happen.
 *
 * No chatId: a spoken turn is not part of anybody's typed conversation, and
 * threading it into one would put the robot's own errands in a user's history.
 */
async function runAgentOnUtterance(utterance: string): Promise<void> {
  const messages: UIMessage[] = [
    {
      id: newId(),
      role: "user",
      parts: [{ type: "text", text: utterance }],
    },
  ];
  const result = await runAgentChat(messages);
  await result.consumeStream();
}

let loop: VoiceLoop | null = null;

function getLoop(): VoiceLoop {
  if (loop === null) {
    loop = new VoiceLoop({
      callTool: (name, args) => callTool(name, args),
      runAgent: runAgentOnUtterance,
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
      // Who switched it on, in the log. The robot is about to act on what it
      // overhears in a shared lab, and that should have a name attached.
      console.log(`[voice] start requested by ${user.id}`);
      l.start();
      return l.snapshot();
    },
    {
      auth: true,
      detail: {
        summary:
          "Start the voice loop: heard speech is fed to the agent until stopped.",
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
      return l.snapshot();
    },
    {
      auth: true,
      detail: {
        summary:
          "Stop the voice loop. The robot keeps listening; nothing acts.",
        tags: ["voice"],
      },
    },
  )
  .get("/status", () => getLoop().snapshot(), {
    auth: true,
    detail: {
      summary:
        "Whether the loop is running, and what it has heard and done so far.",
      tags: ["voice"],
    },
  });
