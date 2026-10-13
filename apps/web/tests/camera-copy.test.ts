/**
 * The robot camera's operator copy.
 *
 * Pinned because two pages render this feed and one of them printed the raw
 * token ("Sin señal — network"), called a camera that never produced a frame
 * "congelada", and labelled a blank canvas EN VIVO when the <img> failed.
 */

import { describe, expect, test } from "bun:test";
import {
  cameraBaseFromUrl,
  describeRobotCam,
  isMixedContent,
  parseHttpDetail,
  plainText,
  robotCamBadge,
  snapshotFailure,
} from "../src/lib/robot/camera-copy";
import type { RobotCamStatus } from "../src/lib/robot/mjpeg-camera";

const URL_ = "http://g1-orin.local:8001/camera/stream.mjpg?c=3";

const status = (over: Partial<RobotCamStatus> = {}): RobotCamStatus => ({
  live: false,
  frame_age_s: null,
  frames: 0,
  width: 1920,
  height: 1080,
  stream_width: 1920,
  stream_height: 1080,
  stale_after_s: 1,
  source: "none",
  ...over,
});

function text(input: Parameters<typeof describeRobotCam>[0]): string {
  const o = describeRobotCam(input).overlay;
  return o && o.kind === "message" ? `${o.title} | ${plainText(o.body)}` : "";
}

describe("helpers", () => {
  test("base from a stream url", () => {
    expect(cameraBaseFromUrl(URL_)).toBe("http://g1-orin.local:8001/camera");
    expect(cameraBaseFromUrl(null)).toBeNull();
  });

  test("mixed content is https page + http camera", () => {
    expect(isMixedContent("https:", URL_)).toBe(true);
    expect(isMixedContent("http:", URL_)).toBe(false);
    expect(isMixedContent("https:", "https://x/camera")).toBe(false);
  });

  test("HTTP details keep the bridge's words", () => {
    expect(parseHttpDetail("HTTP 503: no frame yet")).toEqual({
      status: 503,
      words: "no frame yet",
    });
    expect(parseHttpDetail("HTTP 404")).toEqual({ status: 404, words: null });
    expect(parseHttpDetail("network")).toBeNull();
  });
});

describe("badge", () => {
  test("a broken image is never EN VIVO", () => {
    expect(robotCamBadge("live", status({ live: true, frames: 9 }))).toBe(
      "live",
    );
    expect(robotCamBadge("live", status({ live: true, frames: 9 }), true)).toBe(
      "down",
    );
  });

  test("frozen only if something was ever on screen", () => {
    expect(robotCamBadge("stale", status({ frames: 40 }))).toBe("stale");
    expect(robotCamBadge("stale", status({ frames: 0 }))).toBe("down");
  });
});

describe("overlay copy", () => {
  test("live and loading: no overlay", () => {
    const c = describeRobotCam({
      state: "live",
      detail: null,
      status: status({ live: true, frames: 3 }),
      url: URL_,
    });
    expect(c.overlay).toBeNull();
  });

  test("never-had-a-frame is not 'congelada', and carries the bridge hint", () => {
    const c = describeRobotCam({
      state: "stale",
      detail: "la cámara todavía no entregó ningún cuadro",
      status: status({ frames: 0, hint: "neither camera feed is live." }),
      url: URL_,
    });
    expect(c.badge).toBe("down");
    expect(c.overlay?.kind).toBe("message");
    if (c.overlay?.kind === "message") {
      expect(c.overlay.title).not.toContain("congelada");
      expect(c.overlay.bridgeText).toBe("neither camera feed is live.");
      expect(c.overlay.seeThrough).toBe(false);
    }
  });

  test("a frozen picture names its source and stays visible", () => {
    const c = describeRobotCam({
      state: "stale",
      detail: "sin cuadros hace 4.0 s",
      status: status({ frames: 120, source: "videohub" }),
      url: URL_,
    });
    expect(c.badge).toBe("stale");
    if (c.overlay?.kind === "message") {
      expect(c.overlay.seeThrough).toBe(true);
      // Named for an operator (which camera), not by its program name.
      expect(plainText(c.overlay.body)).toContain("cámara del fabricante");
    }
  });

  test("network, CORS and mixed content are three different messages", () => {
    const net = text({ state: "error", detail: "network", url: URL_ });
    const cors = text({
      state: "error",
      detail: "cors",
      url: URL_,
      pageOrigin: "http://localhost:3001",
    });
    const mixed = text({
      state: "error",
      detail: "network",
      url: URL_,
      pageProtocol: "https:",
    });
    expect(net).toContain("g1-orin.local");
    expect(net).not.toContain("network");
    expect(cors).toContain("BRIDGE_CORS_ORIGINS");
    expect(cors).toContain("http://localhost:3001");
    expect(mixed).toContain("HTTPS");
    expect(new Set([net, cors, mixed]).size).toBe(3);
  });

  test("a 404 points at the URL misconfiguration", () => {
    expect(text({ state: "error", detail: "HTTP 404", url: URL_ })).toContain(
      "PUBLIC_ROBOT_CAM_URL",
    );
  });

  test("a 503 shows the bridge's own sentence", () => {
    const c = describeRobotCam({
      state: "error",
      detail: "HTTP 503: the head camera is real-hardware only",
      url: URL_,
    });
    if (c.overlay?.kind === "message") {
      expect(c.overlay.bridgeText).toBe(
        "the head camera is real-hardware only",
      );
      // A sentence we know leads in Spanish; the English stays underneath.
      expect(c.overlay.bridgeEs).toContain("robot real");
    }
  });

  test("an unknown bridge sentence stays verbatim, untranslated", () => {
    const c = describeRobotCam({
      state: "error",
      detail: "HTTP 503: relay restarting",
      url: URL_,
    });
    if (c.overlay?.kind === "message") {
      expect(c.overlay.bridgeText).toBe("relay restarting");
      expect(c.overlay.bridgeEs).toBeNull();
    }
  });

  test("a broken image while live is explained, not blank", () => {
    const c = describeRobotCam({
      state: "live",
      detail: null,
      status: status({ live: true, frames: 5 }),
      url: URL_,
      broken: true,
    });
    expect(c.badge).toBe("down");
    expect(c.overlay?.kind).toBe("message");
  });
});

describe("sim feed copy", () => {
  test("unreachable names both causes, not just the certificate", async () => {
    const { describeSimCam } = await import("../src/lib/robot/camera-copy");
    const c = describeSimCam("error", "unreachable", "10.40.5.4:60001");
    expect(c.certLinks).toBe(true);
    if (c.overlay?.kind === "message") {
      const t = plainText(c.overlay.body);
      expect(t).toContain("Certificado");
      expect(t).toContain("simulador apagado");
    }
  });

  test("'sin video' has its own message: the server DID answer", async () => {
    const { describeSimCam } = await import("../src/lib/robot/camera-copy");
    const c = describeSimCam("error", "sin video", "h:60001");
    expect(c.certLinks).toBe(false);
    if (c.overlay?.kind === "message") {
      expect(c.overlay.title).toBe("El servidor no envió cuadros");
    }
  });

  test("an /offer that never answers is its own message", async () => {
    const { describeSimCam } = await import("../src/lib/robot/camera-copy");
    const c = describeSimCam("error", "timeout", "h:60001");
    expect(c.certLinks).toBe(false);
    if (c.overlay?.kind === "message") {
      expect(c.overlay.title).toBe("El simulador no responde");
      expect(plainText(c.overlay.body)).toContain("5 s");
    }
  });

  test("a disconnected feed is frozen, not live", async () => {
    const { describeSimCam } = await import("../src/lib/robot/camera-copy");
    const c = describeSimCam("stale", "conexión interrumpida", "h:60001");
    expect(c.badge).toBe("stale");
    expect(c.overlay?.kind).toBe("message");
  });
});

describe("snapshot failures", () => {
  test("never the raw English as the message", () => {
    const timeout = snapshotFailure(
      new DOMException("signal timed out", "TimeoutError"),
    );
    expect(timeout.es).toBe("La cámara no respondió a tiempo.");

    const net = snapshotFailure(new TypeError("Failed to fetch"));
    expect(net.es).toContain("No se pudo alcanzar");
    expect(net.raw).toBe("Failed to fetch");

    const bridge = snapshotFailure(new Error("no frame yet"));
    expect(bridge.es).toContain("cuadro");
    expect(bridge.raw).toBe("no frame yet"); // verbatim, for the readout

    const http = snapshotFailure(new Error("HTTP 502"));
    expect(http.es).toBe("La cámara respondió HTTP 502.");
    expect(http.raw).toBeNull();

    const unknown = snapshotFailure(new Error("relay exploded"));
    expect(unknown.es).toBe("La cámara respondió con un error.");
    expect(unknown.raw).toBe("relay exploded");
  });
});
