import { describe, expect, test } from "bun:test";
import { Pcm16Resampler } from "./pcm";

function encode(samples: number[]): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  samples.forEach((sample, index) => view.setInt16(index * 2, sample, true));
  return bytes;
}

function count(bytes: Uint8Array): number {
  return bytes.byteLength / 2;
}

describe("Pcm16Resampler", () => {
  test("keeps the expected long-run sample count in both directions", () => {
    const source16k = encode(Array.from({ length: 16_000 }, (_, i) => (i % 200) - 100));
    const up = new Pcm16Resampler(16_000, 24_000).push(source16k);
    expect(count(up)).toBeGreaterThanOrEqual(23_998);
    expect(count(up)).toBeLessThanOrEqual(24_000);

    const down = new Pcm16Resampler(24_000, 16_000).push(up);
    expect(count(down)).toBeGreaterThanOrEqual(15_997);
    expect(count(down)).toBeLessThanOrEqual(16_000);
  });

  test("preserves interpolation state across arbitrary network chunks", () => {
    const source = encode(Array.from({ length: 1_000 }, (_, i) => i * 20 - 10_000));
    const oneShot = new Pcm16Resampler(16_000, 24_000).push(source);
    const chunked = new Pcm16Resampler(16_000, 24_000);
    const parts = [
      chunked.push(source.slice(0, 246)),
      chunked.push(source.slice(246, 1_110)),
      chunked.push(source.slice(1_110)),
    ];
    const joined = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
    let offset = 0;
    for (const part of parts) {
      joined.set(part, offset);
      offset += part.byteLength;
    }
    expect(Array.from(joined)).toEqual(Array.from(oneShot));
  });

  test("rejects half a PCM sample", () => {
    expect(() => new Pcm16Resampler(16_000, 24_000).push(new Uint8Array([1]))).toThrow();
  });
});
