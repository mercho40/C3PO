import { VoiceConversation } from "../../../../../apps/back/src/voice/conversation.ts";
let n = 0;
const c = new VoiceConversation({
  newId: () => String(n++),
  speak: async () => {
    throw new Error("say: bridge refused");
  },
  complete: async () =>
    (async function* () {
      yield "Hola. ";
      await new Promise((r) => setTimeout(r, 200));
      yield "Segunda frase.";
    })(),
});
c.turn("hola")
  .then((m) => console.log("ok", m))
  .catch((e) => console.log("turn rejected:", e.message));
setTimeout(() => console.log("alive after 1s"), 1000);
