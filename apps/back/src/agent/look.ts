/**
 * `look` — the agent sees through the robot's camera.
 *
 * Until this existed the agent only ever got the detector's labels and ranges
 * (`describe_surroundings`): eighty COCO classes, no doors, no text, no colour,
 * nothing it could be asked about that YOLO was not trained to name. This hands
 * the newest camera frame to a vision model together with a question, and
 * returns the model's answer as text.
 *
 * IT RUNS IN `back`, NOT ON THE ROBOT, for the same reason the agent does: the
 * model key lives here and the robot holds no credentials (D6.2). The bridge
 * serves the frame from /camera/cloud_frame.jpg, faces already blurred (OpenCV
 * is on the robot, BACKLOG §12); a 503 there means it could not blur, and the
 * frame is not sent.
 *
 * TEXT OUT, NEVER THE IMAGE. The answer is what returns to the agent loop, not
 * the frame: a JPEG in a tool result is base64 in the conversation, ~27 k
 * characters for one 640×480 frame, replayed on every later step of a model
 * whose whole window is 32 k tokens (`tic-chat`). A separate, single-shot
 * vision call keeps the picture out of the loop entirely.
 *
 * ABSENT IS NOT EMPTY (D7). No live frame is `unavailable` with the bridge's
 * own reason — never a description of an empty room. And the answer is a
 * model's reading of one picture: the tool says so, and points at
 * `describe_surroundings` for anything measured.
 *
 * Not a motion tool, so no danger gate — but still audited like every tool
 * call, because what the agent was shown is part of why it acted.
 */

import { bridgeSiblingUrl } from "#back/bridge/url.js";

export const LOOK_TOOL = {
  name: "look",
  description: [
    "Look through the robot's camera and answer a question about what is",
    "visible right now: objects, people, colours, text on a sign, whether a",
    "door is open. A vision model reads the newest camera frame. Use it when the",
    "operator asks what the robot sees, or before acting on something you have",
    "not seen. It is one model's reading of one picture and can be wrong; for",
    "distances and bearings use describe_surroundings, which is measured. Real",
    "robot only. Returns status 'unavailable' with the reason when there is no",
    "live picture — that means you cannot see, not that nothing is there.",
  ].join(" "),
  parameters: {
    type: "object",
    properties: {
      question: {
        type: "string",
        description:
          "What to find out from the picture, e.g. '¿Hay una persona?' or 'What colour is the mug on the table?'. Omit for a general description.",
      },
    },
    additionalProperties: false,
  },
} as const;

/** Catalogue line for the system prompt, in `buildSystemPrompt`'s format. */
export const LOOK_PROMPT_LINE = `- ${LOOK_TOOL.name} [back/available/real-only/danger:low] — ${LOOK_TOOL.description}`;

const DEFAULT_QUESTION =
  "Describí brevemente lo que se ve y nombrá cada objeto o persona.";

/**
 * Told to the vision model, not the agent. The viewpoint note is measured, not
 * decorative: on the first probe the robot's own feet at the bottom edge were
 * described as "the footrests of two chairs".
 */
export const VISION_SYSTEM = [
  "You are the eyes of a Unitree G1 humanoid robot. The image is the newest",
  "frame from the camera mounted on the robot. The robot's own body — its feet",
  "at the bottom edge, its hands or arms at the sides — may appear in the",
  "frame: name it as the robot's own body, never as another object.",
  "Answer the question from what is visible only. If the picture cannot answer",
  "it (out of frame, too dark, blurred, ambiguous), say so plainly instead of",
  "guessing. Do not estimate distances in metres. At most three sentences.",
  "Answer in the language of the question.",
].join(" ");

export type CameraStatus = {
  live?: boolean;
  frame_age_s?: number | null;
  source?: string | null;
  hint?: string | null;
  error?: string;
};

export type LookDeps = {
  /** GET the bridge's /camera/status: `{ ok, status, body }`. */
  cameraStatus: () => Promise<{
    ok: boolean;
    status: number;
    body: CameraStatus;
  }>;
  /** GET the bridge's face-blurred frame; null or a throw (with the reason) when there is none. */
  cameraFrame: () => Promise<Uint8Array | null>;
  /** One vision call: the question and the JPEG in, the answer text out. */
  describe: (question: string, jpeg: Uint8Array) => Promise<string>;
  /** The model's name, for the result. */
  model: string;
};

export type LookResult =
  | {
      status: "ok";
      answer: string;
      question: string;
      frame_age_s: number | null;
      source: string | null;
      model: string;
      latency_ms: number;
    }
  | { status: "unavailable"; reason: string; hint: string | null }
  | { status: "error"; error: string };

export async function look(
  args: { question?: unknown },
  deps: LookDeps,
): Promise<LookResult> {
  const question =
    typeof args.question === "string" && args.question.trim()
      ? args.question.trim()
      : DEFAULT_QUESTION;

  let camera: Awaited<ReturnType<LookDeps["cameraStatus"]>>;
  try {
    camera = await deps.cameraStatus();
  } catch {
    return {
      status: "unavailable",
      reason:
        "the bridge did not answer, so there is no camera to look through",
      hint: null,
    };
  }
  if (!camera.ok || camera.body.live !== true) {
    return {
      status: "unavailable",
      reason:
        camera.body.error ??
        (camera.ok
          ? "the camera has no live picture"
          : `camera status HTTP ${camera.status}`),
      hint: camera.body.hint ?? null,
    };
  }

  let frame: Uint8Array | null;
  try {
    frame = await deps.cameraFrame();
  } catch (err) {
    return {
      status: "unavailable",
      reason: err instanceof Error ? err.message : String(err),
      hint: camera.body.hint ?? null,
    };
  }
  if (!frame || frame.byteLength === 0) {
    return {
      status: "unavailable",
      reason: "the camera reported live but returned no frame",
      hint: camera.body.hint ?? null,
    };
  }

  const started = performance.now();
  try {
    const answer = (await deps.describe(question, frame)).trim();
    if (!answer)
      return { status: "error", error: "the vision model returned no text" };
    return {
      status: "ok",
      answer,
      question,
      frame_age_s: camera.body.frame_age_s ?? null,
      source: camera.body.source ?? null,
      model: deps.model,
      latency_ms: Math.round(performance.now() - started),
    };
  } catch (err) {
    return {
      status: "error",
      error: `vision model failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

/** The bridge half of `LookDeps`, against BRIDGE_URL's host. */
export const bridgeCamera: Pick<LookDeps, "cameraStatus" | "cameraFrame"> = {
  async cameraStatus() {
    const res = await fetch(bridgeSiblingUrl("/camera/status"), {
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    const body = (await res.json().catch(() => ({}))) as CameraStatus;
    return { ok: res.ok, status: res.status, body };
  },
  async cameraFrame() {
    // Never /camera/frame.jpg: this frame goes to a cloud model.
    const res = await fetch(bridgeSiblingUrl("/camera/cloud_frame.jpg"), {
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as {
        detail?: string;
        error?: string;
      };
      throw new Error(
        body.detail ?? body.error ?? `camera frame HTTP ${res.status}`,
      );
    }
    return new Uint8Array(await res.arrayBuffer());
  },
};

/**
 * `verify` — a yes/no check on the newest frame, through the same camera and
 * vision model as `look`. The bridge's Gemini `verify` needed a paid key; this
 * one uses the school gateway. The model answers in a fixed shape and
 * `verdict` reads it: anything it cannot read as yes or no is `unsure`, never a
 * guessed yes.
 */
export const VERIFY_TOOL = {
  name: "verify",
  description: [
    "Check one fact in the robot's camera view right now and get yes / no /",
    "unsure with a short reason, e.g. after pick: '¿la mano derecha sostiene la",
    "taza?'. Use it after a step instead of assuming it worked. 'unsure' means the",
    "picture cannot show it (out of view, occluded, dark): move or turn and check",
    "again. One model's reading of one picture; status 'unavailable' means you",
    "cannot see, not that the answer is no.",
  ].join(" "),
  parameters: {
    type: "object",
    properties: {
      question: {
        type: "string",
        description:
          "A yes/no question about the current picture, any language: '¿la puerta está abierta?'.",
      },
    },
    required: ["question"],
    additionalProperties: false,
  },
} as const;

export const VERIFY_PROMPT_LINE = `- ${VERIFY_TOOL.name} [back/available/real-only/danger:low] — ${VERIFY_TOOL.description}`;

const VERIFY_FORMAT =
  "Respondé empezando con UNA palabra: «sí», «no» o «no sé» (si la imagen no permite saberlo). Después, una sola frase con la razón, según lo que se ve.";

export type Verdict = "yes" | "no" | "unsure";

/** The model's answer read as a verdict. Anything unclear is "unsure". */
export function verdict(text: string): { answer: Verdict; reason: string } {
  const t = text.trim().normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
  const m = /^[«"'¡¿*\s]*(no se|no lo se|unsure|not sure|si|yes|no)\b/u.exec(t);
  const word = m?.[1];
  const answer: Verdict =
    word === "si" || word === "yes" ? "yes" : word === "no" ? "no" : "unsure";
  const reason = (m ? text.trim().slice(m[0].length) : text.trim())
    .replace(/^[»"'.,:;!\s\-–—]+/u, "")
    .trim();
  return { answer, reason };
}

export type VerifyResult =
  | (Omit<Extract<LookResult, { status: "ok" }>, "answer"> & {
      answer: Verdict;
      reason: string;
    })
  | Exclude<LookResult, { status: "ok" }>;

export async function verify(
  args: { question?: unknown },
  deps: LookDeps,
): Promise<VerifyResult> {
  const question =
    typeof args.question === "string" ? args.question.trim() : "";
  if (!question) return { status: "error", error: "verify needs a question" };
  const seen = await look(
    { question: `${question}\n\n${VERIFY_FORMAT}` },
    deps,
  );
  if (seen.status !== "ok") return seen;
  return { ...seen, question, ...verdict(seen.answer) };
}
