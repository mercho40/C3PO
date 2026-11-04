const R = "../../../../../apps/back/src";
const { RealtimeVoiceSession } = await import(R + "/voice/realtime.ts");
const { Pcm16Resampler } = await import(R + "/voice/pcm.ts");
try {
  new Pcm16Resampler(16000, 24000).push(new Uint8Array(1023));
} catch (e) {
  console.log("odd chunk:", (e as Error).message);
}
const s: any = new RealtimeVoiceSession();
s.state.running = true;
const delta = Buffer.from(new Uint8Array(9600)).toString("base64"); // 200ms @24k
s.receive(
  Buffer.from(JSON.stringify({ type: "response.output_audio.delta", delta })),
);
// more audio keeps arriving for 500ms before output_audio.done
let i = 0;
const t = setInterval(() => {
  s.receive(
    Buffer.from(JSON.stringify({ type: "response.output_audio.delta", delta })),
  );
  if (++i > 5) clearInterval(t);
}, 100);
setTimeout(() => console.log("alive after 1.5s"), 1500);
