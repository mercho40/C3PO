/**
 * Microphone samples -> 16 kHz mono 16-bit WAV, for `POST /voice/transcribe`.
 *
 * Encoded here rather than with MediaRecorder so the codec is never a question:
 * Quest Browser's MediaRecorder picks webm/opus, and whether the gateway takes
 * it is one more thing to find out in a headset. A WAV of PCM is what every
 * speech-to-text accepts. Pure, so it is tested without an AudioContext.
 */

export const TARGET_RATE = 16_000;

/** Average-down resampling. Good enough for speech; the input is ~48 kHz. */
export function downsample(
  input: Float32Array,
  fromRate: number,
  toRate = TARGET_RATE,
): Float32Array {
  if (fromRate === toRate) return input;
  if (fromRate < toRate) throw new Error("upsampling is not supported");
  const ratio = fromRate / toRate;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

/**
 * Drop the silence before and after the speech: B/Y goes down before the
 * first word and comes up after the last, and every second of that is
 * uploaded and transcribed for nothing. `margin` seconds stay on either side
 * so word edges are not clipped. Empty when nothing reaches `level` (peak,
 * about -40 dBFS: the mic runs with noise suppression on).
 */
export function trimSilence(
  samples: Float32Array,
  rate = TARGET_RATE,
  level = 0.01,
  margin = 0.2,
): Float32Array {
  const loud = (v: number) => Math.abs(v) >= level;
  const first = samples.findIndex(loud);
  if (first < 0) return samples.subarray(0, 0);
  const last = samples.findLastIndex(loud);
  const pad = Math.round(margin * rate);
  return samples.subarray(
    Math.max(0, first - pad),
    Math.min(samples.length, last + 1 + pad),
  );
}

/** Concatenate the recorder's chunks. */
export function concat(chunks: readonly Float32Array[]): Float32Array {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out;
}

/** RIFF/WAVE, PCM 16-bit little-endian, mono. Samples are clipped to [-1, 1]. */
export function encodeWav(
  samples: Float32Array,
  rate = TARGET_RATE,
): Uint8Array {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const ascii = (at: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i));
  };
  ascii(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  v.setUint32(16, 16, true); // fmt chunk size
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); // byte rate
  v.setUint16(32, 2, true); // block align
  v.setUint16(34, 16, true); // bits per sample
  ascii(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buf);
}
