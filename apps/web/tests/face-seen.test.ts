import { describe, expect, test } from "bun:test";
import {
  FACE_POLL_MS,
  containRect,
  drawFaceBoxes,
  faceLook,
  faceSeenUrl,
  freshFaces,
  placeBox,
  pollFaceSeen,
  type FaceSeen,
  type SeenFace,
} from "../src/lib/robot/face-seen";
import { camCredentials } from "../src/lib/robot/mjpeg-camera";

const face = (over: Partial<SeenFace> = {}): SeenFace => ({
  box: [100, 50, 40, 60],
  name: "Ana",
  role: "operator",
  known: true,
  live: true,
  ...over,
});

const seen = (over: Partial<FaceSeen> = {}): FaceSeen => ({
  stamp: 1,
  width: 640,
  height: 480,
  age_s: 0.3,
  faces: [face()],
  ...over,
});

describe("faceSeenUrl: the same host as the picture", () => {
  test("strips the trailing /camera", () => {
    expect(faceSeenUrl("http://localhost:8001/camera")).toBe(
      "http://localhost:8001/face/seen",
    );
    expect(faceSeenUrl(" http://localhost:8001/camera/ ")).toBe(
      "http://localhost:8001/face/seen",
    );
  });
  test("a base without /camera keeps its path", () => {
    expect(faceSeenUrl("http://h:9000/x")).toBe("http://h:9000/x/face/seen");
  });
});

describe("camCredentials", () => {
  test("cookie only for apps/back, never for the bridge", () => {
    expect(
      camCredentials("http://localhost:3000/camera", "http://localhost:3000"),
    ).toBe("include");
    expect(
      camCredentials("http://localhost:8001/camera", "http://localhost:3000"),
    ).toBe("omit");
  });
});

describe("overlay geometry", () => {
  test("containRect letterboxes a 4:3 frame in a wide box", () => {
    expect(containRect(1000, 300, 640, 480)).toEqual({
      x: 300,
      y: 0,
      w: 400,
      h: 300,
    });
  });
  test("containRect pillarboxes a 16:9 frame in a tall box", () => {
    const r = containRect(320, 400, 1920, 1080);
    expect(r.x).toBe(0);
    expect(r.w).toBe(320);
    expect(r.h).toBe(180);
    expect(r.y).toBe(110);
  });
  test("containRect with no size draws nothing", () => {
    expect(containRect(100, 100, 0, 0).w).toBe(0);
  });
  test("placeBox maps frame pixels into the displayed picture", () => {
    const rect = containRect(1000, 300, 640, 480); // x 300, scale 0.625
    expect(placeBox([64, 48, 32, 96], 640, 480, rect)).toEqual({
      x: 340,
      y: 30,
      w: 20,
      h: 60,
    });
  });
});

describe("labels and colours", () => {
  test("known by name is green", () => {
    const l = faceLook(face());
    expect(l.text).toBe("Ana");
    expect(l.color).toBe("rgb(74, 222, 128)");
    expect(l.dim).toBe(false);
  });
  test("unknown is amber «Desconocido»", () => {
    const l = faceLook(face({ known: false, name: null, role: null }));
    expect(l.text).toBe("Desconocido");
    expect(l.color).toBe("rgb(251, 191, 36)");
  });
  test("live false says ¿foto? and dims", () => {
    const l = faceLook(face({ live: false }));
    expect(l.text).toBe("Ana · ¿foto?");
    expect(l.dim).toBe(true);
    expect(faceLook(face({ live: null })).dim).toBe(false);
  });
});

describe("stale boxes never linger", () => {
  test("fresh answer is drawn", () => {
    expect(freshFaces(seen()).length).toBe(1);
  });
  test("an old answer is not", () => {
    expect(freshFaces(seen({ age_s: 2.5 })).length).toBe(0);
  });
  test("an answer that has aged since it arrived expires", () => {
    expect(freshFaces(seen({ age_s: 0.5 }), 1.4).length).toBe(1);
    expect(freshFaces(seen({ age_s: 0.5 }), 1.6).length).toBe(0);
  });
  test("no answer, or a malformed one, draws nothing", () => {
    expect(freshFaces(null)).toEqual([]);
    expect(
      freshFaces({ ...seen(), faces: undefined } as unknown as FaceSeen),
    ).toEqual([]);
  });
});

describe("drawFaceBoxes", () => {
  test("one box and one label per face, in the face's colour", () => {
    const calls: { fn: string; args: unknown[]; color?: string }[] = [];
    const ctx = {
      strokeStyle: "",
      fillStyle: "",
      lineWidth: 0,
      font: "",
      globalAlpha: 1,
      strokeRect(...args: unknown[]) {
        calls.push({ fn: "strokeRect", args, color: this.strokeStyle });
      },
      fillRect(...args: unknown[]) {
        calls.push({ fn: "fillRect", args });
      },
      fillText(...args: unknown[]) {
        calls.push({ fn: "fillText", args, color: this.fillStyle });
      },
      measureText: () => ({ width: 10 }) as TextMetrics,
    };
    drawFaceBoxes(
      ctx as never,
      [face(), face({ known: false, name: null })],
      640,
      480,
      { x: 0, y: 0, w: 640, h: 480 },
    );
    const boxes = calls.filter((c) => c.fn === "strokeRect");
    expect(boxes.map((b) => b.args)).toEqual([
      [100, 50, 40, 60],
      [100, 50, 40, 60],
    ]);
    expect(boxes[0].color).toBe("rgb(74, 222, 128)");
    expect(boxes[1].color).toBe("rgb(251, 191, 36)");
    expect(
      calls.filter((c) => c.fn === "fillText").map((c) => c.args[0]),
    ).toEqual(["Ana", "Desconocido"]);
    expect(ctx.globalAlpha).toBe(1);
  });
});

describe("pollFaceSeen", () => {
  function harness(opts: { ok?: boolean; hidden?: () => boolean } = {}) {
    const urls: string[] = [];
    const inits: RequestInit[] = [];
    const delays: number[] = [];
    let next: (() => void) | null = null;
    const got: (FaceSeen | null)[] = [];
    const stop = pollFaceSeen(
      "http://localhost:8001/face/seen",
      (s) => got.push(s),
      {
        fetch: (async (url: string, init: RequestInit) => {
          urls.push(url);
          inits.push(init);
          if (opts.ok === false) return new Response("no", { status: 503 });
          return Response.json(seen());
        }) as unknown as typeof fetch,
        hidden: opts.hidden,
        schedule: (fn, ms) => {
          delays.push(ms);
          next = fn;
          return 1;
        },
        cancel: () => (next = null),
      },
    );
    const step = async () => {
      await Bun.sleep(1);
      const fn = next;
      next = null;
      fn?.();
      await Bun.sleep(1);
    };
    return { urls, inits, delays, got, stop, step, pending: () => next };
  }

  test("polls about once a second, with no cookie", async () => {
    const h = harness();
    await h.step();
    await h.step();
    expect(h.urls.length).toBe(3);
    expect(h.inits[0].credentials).toBe("omit");
    expect(h.delays[0]).toBe(FACE_POLL_MS);
    expect(h.got[0]?.faces.length).toBe(1);
    h.stop();
  });

  test("makes no request while hidden", async () => {
    const h = harness({ hidden: () => true });
    await h.step();
    await h.step();
    expect(h.urls.length).toBe(0);
    h.stop();
  });

  test("stop means stop: nothing is scheduled again", async () => {
    const h = harness();
    await Bun.sleep(1);
    h.stop();
    expect(h.pending()).toBeNull();
    const before = h.urls.length;
    await h.step();
    expect(h.urls.length).toBe(before);
  });

  test("errors back off and report null", async () => {
    const h = harness({ ok: false });
    await h.step();
    await h.step();
    expect(h.got).toEqual([null, null, null]);
    expect(h.delays.slice(0, 3)).toEqual([2000, 4000, 8000]);
    h.stop();
  });
});
