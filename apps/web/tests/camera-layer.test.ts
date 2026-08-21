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
  src = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  /** Pretend a frame decoded. */
  arrive(width = 960) {
    this.naturalWidth = width;
    this.onload?.();
  }
}

function withFakeImage<T>(body: (made: FakeImage[]) => T): T {
  const made: FakeImage[] = [];
  const original = globalThis.Image;
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
