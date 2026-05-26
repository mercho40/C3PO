import { defineSkill, t } from "./define";

export const say = defineSkill({
  name: "say",
  description:
    "Speak text aloud through the robot's speakers. Phase 4 (voice loop) replaces the " +
    "current stub with streaming TTS via Cartesia. Today's behaviour: bridge logs the " +
    "request but does not produce audio.",
  parameters: t.Object({
    text: t.String({
      minLength: 1,
      maxLength: 500,
      description: "What the robot should say.",
    }),
    voice: t.Union(
      [
        t.Literal("default"),
        t.Literal("warm"),
        t.Literal("neutral"),
        t.Literal("robotic"),
      ],
      {
        default: "default",
        description:
          "Voice style; ignored by the current stub. Cartesia voice IDs land in Phase 4.",
      },
    ),
  }),
  preconditions: [],
  expectedDurationSeconds: 2,
  cancellable: false,
  typicalFailureModes: ["tts_provider_error", "audio_pipeline_unavailable"],
  classification: "speech",
  dangerLevel: "low",
  status: "stub",
  works: { sim: true, real: true },
});
