import { defineSkill, t } from "./define";

export const say = defineSkill({
  name: "say",
  description:
    "Speak text aloud through the robot's own speaker, using on-robot text-to-speech. " +
    "Real on hardware — no cloud round-trip and no API key. Logged only on sim and stub, " +
    "which have no speaker. " +
    "Reach for this more than you would expect: speech is not gated by the locomotion FSM, " +
    "so it is a channel the robot still has when motion is being refused — and this robot " +
    "gets into that state. Saying what you are about to do, or that you are stuck, beats " +
    "silence when a person is standing next to a humanoid. " +
    "One language per utterance: the firmware has no mixed Chinese/English voice, so send " +
    "separate calls rather than mixing scripts in one string.",
  parameters: t.Object({
    text: t.String({
      minLength: 1,
      maxLength: 500,
      description: "What the robot should say, spoken aloud.",
    }),
    language: t.Optional(
      t.Union([t.Literal("english"), t.Literal("chinese")], {
        default: "english",
        description:
          "Which voice to use. Cannot be mixed within one utterance — the robot has no " +
          "bilingual voice.",
      }),
    ),
  }),
  preconditions: [],
  expectedDurationSeconds: 3,
  cancellable: false,
  typicalFailureModes: ["voice_service_no_answer", "bridge_disconnected"],
  classification: "speech",
  dangerLevel: "low",
  status: "real",
  works: { sim: false, real: true },
});
