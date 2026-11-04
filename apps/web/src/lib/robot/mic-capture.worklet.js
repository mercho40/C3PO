// @ts-nocheck -- AudioWorkletGlobalScope (AudioWorkletProcessor, registerProcessor) has no TS lib.
// Push-to-talk capture for vr-voice.svelte.ts: while `true` was the last message, posts each input block.
registerProcessor(
  "mic-capture",
  class extends AudioWorkletProcessor {
    on = false;
    constructor() {
      super();
      this.port.onmessage = (e) => {
        this.on = e.data === true;
      };
    }
    process(inputs) {
      // No channels before the stream delivers audio or after the track ends; a throw here kills the processor for good.
      const ch = inputs[0]?.[0];
      if (this.on && ch) this.port.postMessage(ch.slice()); // a copy: the engine reuses the array
      return true;
    }
  },
);
