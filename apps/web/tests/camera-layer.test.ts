/**
 * Tests for the XR camera layer.
 *
 * Every failure mode below renders as a BLACK VIEW in a headset, which is
 * indistinguishable from "the camera isn't running" and from "the overlay
 * didn't composite" — the exact confusion that cost a headset session. None of
 * them throw, and none are visible from the code.
 *
 * WebGL is stubbed rather than run. That is honest about what this covers: the
 * setup decisions that silently produce black, not whether pixels arrive. The
 * uncovered half needs a headset, and no test here should be read as standing
 * in for one.
 */

import { describe, expect, test } from "bun:test";
import { CameraLayer } from "../src/lib/webxr/camera-layer";

// --- a WebGL context that records what was asked of it ---------------------

type Call = { fn: string; args: unknown[] };

function stubGl() {
  const calls: Call[] = [];
  const rec =
    (fn: string, ret: unknown = null) =>
    (...args: unknown[]) => {
      calls.push({ fn, args });
      return ret;
    };
  const gl: Record<string, unknown> = {
    TEXTURE_2D: "TEXTURE_2D",
    TEXTURE_WRAP_S: "WRAP_S",
    TEXTURE_WRAP_T: "WRAP_T",
    TEXTURE_MIN_FILTER: "MIN",
    TEXTURE_MAG_FILTER: "MAG",
    CLAMP_TO_EDGE: "CLAMP_TO_EDGE",
    LINEAR: "LINEAR",
    RGB: "RGB",
    UNSIGNED_BYTE: "UBYTE",
    ARRAY_BUFFER: "ARRAY_BUFFER",
    STATIC_DRAW: "STATIC_DRAW",
    VERTEX_SHADER: "VS",
    FRAGMENT_SHADER: "FS",
    COMPILE_STATUS: "COMPILE",
    LINK_STATUS: "LINK",
    TRIANGLES: "TRIANGLES",
    FLOAT: "FLOAT",
    BLEND: "BLEND",
    SRC_ALPHA: "SRC_ALPHA",
    ONE_MINUS_SRC_ALPHA: "1-SRC_ALPHA",
    ONE: "ONE",
    createShader: rec("createShader", {}),
    shaderSource: rec("shaderSource"),
    compileShader: rec("compileShader"),
    getShaderParameter: rec("getShaderParameter", true),
    getShaderInfoLog: rec("getShaderInfoLog", ""),
    deleteShader: rec("deleteShader"),
    createProgram: rec("createProgram", {}),
    attachShader: rec("attachShader"),
    linkProgram: rec("linkProgram"),
    getProgramParameter: rec("getProgramParameter", true),
    getProgramInfoLog: rec("getProgramInfoLog", ""),
    getAttribLocation: rec("getAttribLocation", 0),
    getUniformLocation: rec("getUniformLocation", {}),
    createBuffer: rec("createBuffer", {}),
    bindBuffer: rec("bindBuffer"),
    bufferData: rec("bufferData"),
    createTexture: rec("createTexture", {}),
    bindTexture: rec("bindTexture"),
    texParameteri: rec("texParameteri"),
    texImage2D: rec("texImage2D"),
    useProgram: rec("useProgram"),
    enableVertexAttribArray: rec("enableVertexAttribArray"),
    vertexAttribPointer: rec("vertexAttribPointer"),
    uniform1f: rec("uniform1f"),
    uniform2f: rec("uniform2f"),
    enable: rec("enable"),
    blendFunc: rec("blendFunc"),
    blendFuncSeparate: rec("blendFuncSeparate"),
    drawArrays: rec("drawArrays"),
    disable: rec("disable"),
    deleteTexture: rec("deleteTexture"),
    deleteBuffer: rec("deleteBuffer"),
    deleteProgram: rec("deleteProgram"),
  };
  return { gl: gl as unknown as WebGLRenderingContext, calls };
}

// --- a minimal <img> stand-in ----------------------------------------------

class FakeImage {
  crossOrigin: string | null = null;
  naturalWidth = 0;
  naturalHeight = 0;
  width = 0;
  height = 0;
  src = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  //: The layer attaches the element to the document so Blink keeps advancing
  //: the MJPEG stream — a detached image has no rendering observer and may
  //: stop after the first decoded part. Modelled here so the test exercises
  //: the same lifecycle the browser does.
  attached = false;
  style = { cssText: "" };
  setAttribute() {}
  remove() {
    this.attached = false;
  }
  /** Pretend a frame decoded. */
  arrive(width = 960, height = 720) {
    this.naturalWidth = width;
    this.naturalHeight = height;
    this.onload?.();
  }
}

function withFakeImage<T>(body: (made: FakeImage[]) => T): T {
  const made: FakeImage[] = [];
  const original = globalThis.Image;
  const originalDoc = (globalThis as { document?: unknown }).document;
  // A minimal document, so `setStreamUrl` takes the attach path the browser
  // takes. Without one it silently skips it and the test proves nothing.
  (globalThis as { document?: unknown }).document = {
    body: {
      appendChild: (el: FakeImage) => {
        el.attached = true;
        return el;
      },
    },
  };
  // @ts-expect-error — deliberately swapping the constructor for the test
  globalThis.Image = function () {
    const img = new FakeImage();
    made.push(img);
    return img;
  };
  try {
    return body(made);
  } finally {
    globalThis.Image = original;
    if (originalDoc === undefined) {
      delete (globalThis as { document?: unknown }).document;
    } else {
      (globalThis as { document?: unknown }).document = originalDoc;
    }
  }
}

describe("attach", () => {
  test("sets crossOrigin before src — without it WebGL renders black", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      new CameraLayer(gl).setStreamUrl("http://127.0.0.1:8081/stream.mjpg");
      // A texture sourced from an image the page cannot read back throws on
      // upload. The symptom is a black view, not an error anyone sees.
      expect(made[0].crossOrigin).toBe("anonymous");
      expect(made[0].src).toBe("http://127.0.0.1:8081/stream.mjpg");
    });
  });

  test("reports no frame until one actually decodes", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      expect(layer.hasFrame).toBe(false);
      made[0].arrive();
      expect(layer.hasFrame).toBe(true);
    });
  });

  test("a stream that errors does not report a frame", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].onerror?.();
      expect(layer.hasFrame).toBe(false);
    });
  });
});

describe("draw", () => {
  test("does nothing before a frame arrives, and does not throw", () => {
    withFakeImage(() => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      layer.draw(true);
      expect(calls.some((c) => c.fn === "drawArrays")).toBe(false);
      expect(layer.framesUploaded).toBe(0);
    });
  });

  test("non-power-of-two safety is set — the other silent black", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive(960); // 960x540 is not a power of two
      layer.draw(true);

      const params = calls
        .filter((c) => c.fn === "texParameteri")
        .map((c) => c.args);
      // WebGL1 silently refuses to sample a non-POT texture with REPEAT or
      // mipmaps. It renders black rather than complaining.
      expect(params).toContainEqual(["TEXTURE_2D", "WRAP_S", "CLAMP_TO_EDGE"]);
      expect(params).toContainEqual(["TEXTURE_2D", "WRAP_T", "CLAMP_TO_EDGE"]);
      expect(params).toContainEqual(["TEXTURE_2D", "MIN", "LINEAR"]);
      expect(params).toContainEqual(["TEXTURE_2D", "MAG", "LINEAR"]);
    });
  });

  test("draws opaque in VR and translucent under passthrough", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive();

      layer.draw(true);
      layer.draw(false);
      const alphas = calls
        .filter((c) => c.fn === "uniform1f")
        .map((c) => c.args[1]);
      // Opaque in VR: the camera IS the world. Translucent in AR: covering
      // passthrough would hide the robot the operator is standing next to.
      expect(alphas[0]).toBe(1.0);
      expect(alphas[1]).toBeCloseTo(0.85);
    });
  });

  test("a throwing upload skips the frame instead of ending the session", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      // A tainted or half-decoded image throws here in a real context.
      (gl as unknown as Record<string, unknown>).texImage2D = () => {
        throw new Error("tainted canvas");
      };
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive();
      layer.draw(true); // must not throw
      expect(calls.some((c) => c.fn === "drawArrays")).toBe(false);
    });
  });

  test("counts uploads so the page can tell live from frozen", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive();
      layer.draw(true);
      layer.draw(true);
      expect(layer.framesUploaded).toBe(2);
    });
  });
});

describe("dispose", () => {
  test("cuts the src — otherwise the MJPEG request stays open", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive();
      layer.draw(true);
      layer.dispose();

      // An ended session must stop pulling frames. The request lives for as
      // long as the element points at it.
      expect(made[0].src).toBe("");
      expect(layer.hasFrame).toBe(false);
      for (const fn of ["deleteTexture", "deleteBuffer", "deleteProgram"]) {
        expect(calls.some((c) => c.fn === fn)).toBe(true);
      }
    });
  });

  test("is safe before anything was ever drawn", () => {
    withFakeImage(() => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      layer.dispose(); // must not throw
      expect(layer.hasFrame).toBe(false);
    });
  });
});

describe("reconnection — the failure the source recovers from", () => {
  test("a new URL swaps the image and cuts the old request", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg?c=1");
      made[0].arrive();

      layer.setStreamUrl("http://x/stream.mjpg?c=2");

      // Two elements, and the first one's request is closed. Without cutting
      // it, a session that reconnects a few times holds several open MJPEG
      // streams at once, all crossing the SSH tunnel.
      expect(made.length).toBe(2);
      expect(made[0].src).toBe("");
      expect(made[1].src).toBe("http://x/stream.mjpg?c=2");
      expect(made[1].crossOrigin).toBe("anonymous");
    });
  });

  test("the same URL is ignored — no needless reconnect", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg?c=1");
      layer.setStreamUrl("http://x/stream.mjpg?c=1");
      expect(made.length).toBe(1);
    });
  });

  test("a stale feed is dimmed, not hidden", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive();

      layer.draw(true);
      layer.setLive(false);
      layer.draw(true);

      const alphas = calls
        .filter((c) => c.fn === "uniform1f")
        .map((c) => c.args[1]);
      // Still drawn: losing the picture mid-motion is worse than an obviously
      // old one. But visibly different without reading any text.
      expect(alphas[0]).toBe(1.0);
      expect(alphas[1] as number).toBeLessThan(0.6);
      expect(alphas[1] as number).toBeGreaterThan(0);
    });
  });

  test("dispose clears the url so a later attach reconnects", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      layer.dispose();
      layer.setStreamUrl("http://x/stream.mjpg"); // same URL, but after dispose
      expect(made.length).toBe(2);
    });
  });
});

test("alpha is blended separately, or a dimmed feed goes nearly invisible", () => {
  // The straight `blendFunc(SRC_ALPHA, ONE_MINUS_SRC_ALPHA)` applies SRC_ALPHA
  // to the alpha channel too, so the framebuffer alpha comes out squared. The
  // stale opacity of 0.45 landed at 0.20 under passthrough — the opposite of
  // "dimmed rather than hidden", which exists because losing the view
  // mid-motion is worse than an obviously old one.
  withFakeImage((made) => {
    const { gl, calls } = stubGl();
    const layer = new CameraLayer(gl);
    layer.setStreamUrl("http://x/stream.mjpg");
    made[0].arrive();
    layer.draw(false);

    const separate = calls.filter((c) => c.fn === "blendFuncSeparate");
    const straight = calls.filter((c) => c.fn === "blendFunc");
    expect(separate.length).toBeGreaterThan(0);
    expect(straight.length).toBe(0);
    // The alpha SOURCE factor must be ONE, not SRC_ALPHA — that is the fix.
    expect(separate[0].args[2]).toBe("ONE");
  });
});

describe("the stream element is attached, and cleaned up", () => {
  test("it goes into the document, or Blink may stop advancing the stream", () => {
    // An MJPEG stream in an <img> advances through the same machinery as an
    // animated image, and Blink pauses that for elements with no rendering
    // observer. A detached image is exactly that: onload fires for the first
    // decoded part, so everything reports healthy, and the bitmap may then
    // never change again — a live camera showing one frozen moment.
    withFakeImage((made) => {
      const { gl } = stubGl();
      new CameraLayer(gl).setStreamUrl("http://x:8081/stream.mjpg");
      expect(made[0].attached).toBe(true);
    });
  });

  test("it is 1x1 and transparent, not display:none", () => {
    // display:none has no rendering observer either, and would reintroduce
    // exactly the problem attaching is meant to solve.
    withFakeImage((made) => {
      const { gl } = stubGl();
      new CameraLayer(gl).setStreamUrl("http://x:8081/stream.mjpg");
      expect(made[0].style.cssText).not.toContain("display:none");
      expect(made[0].style.cssText).toContain("opacity:0");
      expect(made[0].width).toBe(1);
    });
  });

  test("swapping the URL removes the old element", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x:8081/stream.mjpg?c=1");
      layer.setStreamUrl("http://x:8081/stream.mjpg?c=2");

      expect(made[0].attached).toBe(false);
      expect(made[1].attached).toBe(true);
    });
  });

  test("dispose removes it, so a session leaves nothing behind", () => {
    withFakeImage((made) => {
      const { gl } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x:8081/stream.mjpg");
      layer.dispose();

      expect(made[0].attached).toBe(false);
      expect(made[0].src).toBe("");
    });
  });
});

describe("aspect fit — the picture that was live, correct and deformed", () => {
  /** The x,y passed to `u_scale` on the most recent draw. */
  function lastScale(calls: { fn: string; args: unknown[] }[]) {
    const c = calls.filter((c) => c.fn === "uniform2f").at(-1);
    return c ? [c.args[1] as number, c.args[2] as number] : null;
  }

  test("the D435i's 4:3 frame in a Quest eye keeps its proportions", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive(640, 480); // the D435i's actual output

      layer.draw(true, 1680, 1760); // a Quest eye viewport: TALLER than 4:3
      const [sx, sy] = lastScale(calls)!;
      // The image is relatively WIDER than the eye, so width is the binding
      // constraint: fill it, and take bars top and bottom. Shrinking x instead
      // would squeeze the picture — the deformation being fixed here.
      expect(sx).toBe(1);
      expect(sy).toBeCloseTo(1680 / 1760 / (640 / 480), 5);
      expect(sy).toBeLessThan(1);
      // And the aspect actually drawn matches the source: the whole point.
      expect((1680 * sx) / (1760 * sy)).toBeCloseTo(640 / 480, 5);
    });
  });

  test("a wide frame in a squarer eye is letterboxed", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive(1920, 1080); // 16:9

      layer.draw(true, 1000, 1000); // square eye
      const [sx, sy] = lastScale(calls)!;
      expect(sx).toBe(1);
      expect(sy).toBeCloseTo(1 / (1920 / 1080), 5);
      expect(sy).toBeLessThan(1);
    });
  });

  test("a frame matching the eye fills it exactly", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive(640, 480);

      layer.draw(true, 1280, 960); // same 4:3
      expect(lastScale(calls)).toEqual([1, 1]);
    });
  });

  test("never scales ABOVE 1 — the quad stays inside clip space", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive(640, 480);

      for (const [w, h] of [
        [100, 4000],
        [4000, 100],
        [1, 1],
        [1920, 1080],
      ]) {
        layer.draw(true, w, h);
        const [sx, sy] = lastScale(calls)!;
        expect(sx).toBeLessThanOrEqual(1);
        expect(sy).toBeLessThanOrEqual(1);
        expect(sx).toBeGreaterThan(0);
        expect(sy).toBeGreaterThan(0);
      }
    });
  });

  test("a zero dimension falls back to full field, NOT to NaN", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      // A frame whose height never decoded. Dividing by it gives NaN, and a
      // NaN vertex position draws NOTHING — the black view this module exists
      // to prevent, reintroduced by the fix for a distorted one.
      made[0].arrive(640, 0);

      layer.draw(true, 1680, 1760);
      const [sx, sy] = lastScale(calls)!;
      expect(Number.isNaN(sx)).toBe(false);
      expect(Number.isNaN(sy)).toBe(false);
      expect([sx, sy]).toEqual([1, 1]);
    });
  });

  test("no viewport given keeps the old full-field behaviour", () => {
    withFakeImage((made) => {
      const { gl, calls } = stubGl();
      const layer = new CameraLayer(gl);
      layer.setStreamUrl("http://x/stream.mjpg");
      made[0].arrive(640, 480);

      layer.draw(true); // non-XR callers pass no viewport
      expect(lastScale(calls)).toEqual([1, 1]);
    });
  });
});
