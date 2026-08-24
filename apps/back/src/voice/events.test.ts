import { describe, expect, test } from "bun:test";
import { parseVoiceEvents } from "./events";

function stream(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("parseVoiceEvents", () => {
  test("parses events split across arbitrary network chunks", async () => {
    const events = await Array.fromAsync(
      parseVoiceEvents(
        stream(
          ': keepalive\n\ndata: {"seq":1,"kind":"spe',
          'ech","text":"hola"}\n\ndata: {"seq":2,"kind":"stop","text":"emergencia"}\n\n',
        ),
      ),
    );
    expect(events).toEqual([
      { seq: 1, kind: "speech", text: "hola" },
      { seq: 2, kind: "stop", text: "emergencia" },
    ]);
  });

  test("ignores malformed frames without killing the stream", async () => {
    const events = await Array.fromAsync(
      parseVoiceEvents(
        stream(
          "data: nope\n\n",
          'data: {"seq":3,"kind":"speech","text":"seguimos"}\n\n',
        ),
      ),
    );
    expect(events.map((event) => event.text)).toEqual(["seguimos"]);
  });
});
