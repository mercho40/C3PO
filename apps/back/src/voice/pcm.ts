export class Pcm16Resampler {
  private samples: number[] = [];
  // HTTP chunk boundaries fall wherever the transport put them, not on samples:
  // the robot's mic stream does hand over odd-length chunks, and refusing one
  // used to end the whole Realtime session. The stray byte waits for its pair.
  private carry: number | null = null;
  private position = 0;
  private readonly step: number;

  constructor(inputRate: number, outputRate: number) {
    if (inputRate <= 0 || outputRate <= 0)
      throw new Error("sample rates must be positive");
    this.step = inputRate / outputRate;
  }

  push(chunk: Uint8Array): Uint8Array {
    let pcm = chunk;
    if (this.carry !== null && pcm.byteLength > 0) {
      pcm = concatBytes(Uint8Array.of(this.carry), pcm);
      this.carry = null;
    }
    const whole = pcm.byteLength - (pcm.byteLength % 2);
    if (whole < pcm.byteLength) this.carry = pcm[whole]!;
    const view = new DataView(pcm.buffer, pcm.byteOffset, whole);
    for (let offset = 0; offset < whole; offset += 2) {
      this.samples.push(view.getInt16(offset, true));
    }

    const output: number[] = [];
    while (this.position + 1 < this.samples.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const sample =
        this.samples[left]! * (1 - fraction) +
        this.samples[left + 1]! * fraction;
      output.push(Math.max(-32768, Math.min(32767, Math.round(sample))));
      this.position += this.step;
    }

    const consumed = Math.floor(this.position);
    if (consumed > 0) {
      this.samples = this.samples.slice(consumed);
      this.position -= consumed;
    }

    const encoded = new Uint8Array(output.length * 2);
    const encodedView = new DataView(encoded.buffer);
    output.forEach((sample, index) =>
      encodedView.setInt16(index * 2, sample, true),
    );
    return encoded;
  }
}

export function concatBytes(left: Uint8Array, right: Uint8Array): Uint8Array {
  const joined = new Uint8Array(left.byteLength + right.byteLength);
  joined.set(left);
  joined.set(right, left.byteLength);
  return joined;
}
