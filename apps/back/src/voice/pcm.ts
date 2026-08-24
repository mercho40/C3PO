export class Pcm16Resampler {
  private samples: number[] = [];
  private position = 0;
  private readonly step: number;

  constructor(inputRate: number, outputRate: number) {
    if (inputRate <= 0 || outputRate <= 0) throw new Error("sample rates must be positive");
    this.step = inputRate / outputRate;
  }

  push(pcm: Uint8Array): Uint8Array {
    if (pcm.byteLength % 2 !== 0) throw new Error("PCM16 input must contain complete samples");
    const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    for (let offset = 0; offset < pcm.byteLength; offset += 2) {
      this.samples.push(view.getInt16(offset, true));
    }

    const output: number[] = [];
    while (this.position + 1 < this.samples.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const sample = this.samples[left]! * (1 - fraction) + this.samples[left + 1]! * fraction;
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
    output.forEach((sample, index) => encodedView.setInt16(index * 2, sample, true));
    return encoded;
  }
}

export function concatBytes(left: Uint8Array, right: Uint8Array): Uint8Array {
  const joined = new Uint8Array(left.byteLength + right.byteLength);
  joined.set(left);
  joined.set(right, left.byteLength);
  return joined;
}
