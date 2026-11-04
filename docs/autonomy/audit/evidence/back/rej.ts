let tail: Promise<void> | null = null;
const speak = async () => {
  await new Promise((r) => setTimeout(r, 10));
  throw new Error("say failed");
};
tail = speak();
async function* deltas() {
  for (let i = 0; i < 5; i++) {
    await new Promise((r) => setTimeout(r, 50));
    yield "x";
  }
  throw new Error("model stream failed");
}
async function turn() {
  for await (const d of deltas()) {
  }
  await tail;
}
turn().catch((e) => console.log("turn caught:", e.message));
setTimeout(() => console.log("process still alive at 1s"), 1000);
