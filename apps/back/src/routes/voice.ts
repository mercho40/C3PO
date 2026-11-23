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
 * Guarded by `{ auth: true }`, and `.use(authPlugin)` here rather than relying
 * on the composition root — the rule in CLAUDE.md, and this module reads `user`.
 *
 * The voice stack itself — one loop per process, one engine at a time, the
 * listen-after-greet window — is voice/service.ts. This is its HTTP surface.
 */

import { Elysia, t } from "elysia";

import type { AuthPlugin } from "#back/lib/auth-plugin.js";
import type { VoiceService } from "#back/voice/service.js";
import { MAX_AUDIO_BYTES, TranscribeError } from "#back/voice/transcribe.js";

export const voiceRoutes = (deps: {
  authPlugin: AuthPlugin;
  voice: VoiceService;
}) =>
  new Elysia({ prefix: "/voice" })
    .use(deps.authPlugin)
    .post(
      "/start",
      async ({ user, session, body, status }) => {
        // 1.4: driving the robot through a model is an admin's job, like /invoke.
        if (user.role !== "admin")
          return status(403, { error: "admin_required" });
        const r = await deps.voice.start(
          user,
          session.activeOrganizationId ?? null,
          body?.engine,
        );
        return r.status === 200 ? r.body : status(r.status, r.body);
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
        const r = await deps.voice.stop(user);
        return r.status === 200 ? r.body : status(r.status, r.body);
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
          // Stage times for the headset's voice latency (the Quest logs the rest).
          const since = (t: number) => Math.round(performance.now() - t);
          const startedAt = performance.now();
          const audio = await request.arrayBuffer();
          const uploadedAt = performance.now();
          const text = await deps.voice.transcribe(
            audio,
            request.headers.get("content-type") ?? "audio/wav",
          );
          const stages = `[voice] transcribe ${audio.byteLength} B: upload ${Math.round(uploadedAt - startedAt)} ms, transcribe ${since(uploadedAt)} ms`;
          // The admin's OWN push-to-talk: «confirmo» runs the one refused call
          // this client shows, decided by voice/service.ts, never by the model.
          const decision = deps.voice.decideSpoken(text, user, query);
          if (decision.kind !== "confirm") console.log(stages);
          if (decision.kind === "none") return { text };
          server?.timeout(request, 0); // the confirmed call may outlast Bun's 10 s idle cut
          const confirmAt = performance.now();
          const confirmation = await deps.voice.applySpoken(decision, user);
          if (decision.kind === "confirm")
            console.log(`${stages}, confirm ${since(confirmAt)} ms`);
          return { text, confirmation };
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
    .get("/status", ({ user }) => deps.voice.status(user.id), {
      auth: true,
      detail: {
        summary:
          "Whether the loop is running, and what it has heard and done so far.",
        tags: ["voice"],
      },
    });
