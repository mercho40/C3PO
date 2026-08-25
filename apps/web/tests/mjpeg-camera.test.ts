/**
 * The camera client, against a real HTTP server that behaves like the robot's.
 *
 * The vision container (`apps/perception/vision/c3po_vision/stream.py`) has one
 * behaviour that shapes everything here: it **ends the HTTP response** after
 * ~1 s without a frame, because that is MJPEG's only in-band way to say "no
 * longer live". An `<img>` answers by freezing on its last frame forever, with
 * no event — so recovery is not automatic, and a client that does not notice
 * shows the operator a photograph and calls it a feed.
 *
 * The fake below mirrors that server's actual contract: same routes, same
 * `/status` shape (checked field-by-field against its `status()`), same
 * live/stale semantics. It is a real server on a real port — the client's own
 * `fetch` runs unmodified.
 */

import { describe, expect, test } from "bun:test";
import { connectRobotCamera, endpoint } from "../src/lib/robot/mjpeg-camera";

type Fake = {
  url: string;
  stop: () => void;
  setLive: (live: boolean) => void;
  /** Stop advancing `frames` while still reporting live — the invisible gap. */
  freezeFrames: (frozen: boolean) => void;
  /** Every stream.mjpg URL the client has opened, in order. */
  opened: string[];
};

function fakeVisionServer(): Fake {
  let live = true;
  let frozen = false;
  let frames = 0;
  const opened: string[] = [];

  const server = Bun.serve({
    port: 0,
    fetch(req) {
      const path = new URL(req.url).pathname;
      const cors = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
      };

      if (path === "/status") {
        if (live && !frozen) frames += 1;
        // Field-for-field the shape `stream.py`'s status() returns.
        return Response.json(
          {
            v: 1,
            live,
            frame_age_s: frames > 0 ? (live ? 0.1 : 9.9) : null,
            frames,
            clients: opened.length,
            width: 640,
            height: 480,
            stream_width: 640,
            stream_height: 480,
            stale_after_s: 1.0,
          },
          { headers: cors },
        );
      }
      if (path === "/stream.mjpg") {
        opened.push(req.url);
        return new Response("--frame\r\n", {
          headers: {
            ...cors,
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
          },
        });
      }
      return new Response("not a c3po vision endpoint", {
        status: 404,
        headers: cors,
      });
    },
  });

  return {
    url: `http://127.0.0.1:${server.port}`,
    stop: () => server.stop(true),
    setLive: (v: boolean) => (live = v),
    freezeFrames: (v: boolean) => (frozen = v),
    opened,
  };
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("endpoint", () => {
  test("tolerates a trailing slash or none", () => {
    expect(endpoint("http://x:8081", "status")).toBe("http://x:8081/status");
    expect(endpoint("http://x:8081/", "status")).toBe("http://x:8081/status");
    expect(endpoint("http://x:8081///", "stream.mjpg")).toBe(
      "http://x:8081/stream.mjpg",
    );
  });
});

describe("against a live server", () => {
  test("reports live and hands out a stream URL", async () => {
    const fake = fakeVisionServer();
    const states: string[] = [];
    const urls: string[] = [];
    const handle = connectRobotCamera(fake.url, {
      onState: (s) => states.push(s),
      onStatus: () => {},
      onStreamUrl: (u) => urls.push(u),
    });
    try {
      await wait(1400);
      expect(states[0]).toBe("connecting");
      expect(states).toContain("live");
      expect(urls[0]).toContain("/stream.mjpg?c=1");
    } finally {
      handle.close();
      fake.stop();
    }
  });

  test("parses the real /status shape without loss", async () => {
    const fake = fakeVisionServer();
    let last: Record<string, unknown> | null = null;
    const handle = connectRobotCamera(fake.url, {
      onState: () => {},
      onStatus: (s) => (last = s as unknown as Record<string, unknown>),
      onStreamUrl: () => {},
    });
    try {
      await wait(1200);
      expect(last).not.toBeUndefined();
      for (const k of [
        "live",
        "frame_age_s",
        "frames",
        "clients",
        "stale_after_s",
      ]) {
        expect(Object.keys(last ?? {})).toContainEqual(k);
      }
    } finally {
      handle.close();
      fake.stop();
    }
  });
});

describe("recovery — the bug this file was written for", () => {
  test("a stall then a recovery REOPENS the stream with a new URL", async () => {
    const fake = fakeVisionServer();
    const states: string[] = [];
    const urls: string[] = [];
    const handle = connectRobotCamera(fake.url, {
      onState: (s) => states.push(s),
      onStatus: () => {},
      onStreamUrl: (u) => urls.push(u),
    });
    try {
      await wait(1300);
      expect(states).toContain("live");
      const before = urls.length;

      fake.setLive(false); // the server would now END the response
      await wait(1300);
      expect(states).toContain("stale");

      fake.setLive(true); // camera recovers
      await wait(1600);

      // Without a reopen the <img> keeps pointing at a response the server
      // already closed: the picture stays frozen while /status says "live".
      expect(urls.length).toBeGreaterThan(before);
      expect(urls[urls.length - 1]).toContain("?c=2");
    } finally {
      handle.close();
      fake.stop();
    }
  });

  test("an unreachable server reports error, then recovers on its own", async () => {
    const fake = fakeVisionServer();
    const states: string[] = [];
    const urls: string[] = [];
    const handle = connectRobotCamera(fake.url, {
      onState: (s) => states.push(s),
      onStatus: () => {},
      onStreamUrl: (u) => urls.push(u),
    });
    try {
      await wait(1200);
      const before = urls.length;
      fake.stop(); // tunnel drops
      await wait(1400);
      expect(states).toContain("error");

      // A dead tunnel means the stream died with it — whatever returns needs a
      // fresh connection, not the one that went down with it.
      expect(urls.length).toBe(before);
    } finally {
      handle.close();
    }
  });

  test("close() stops polling and says so", async () => {
    const fake = fakeVisionServer();
    const states: string[] = [];
    const handle = connectRobotCamera(fake.url, {
      onState: (s) => states.push(s),
      onStatus: () => {},
      onStreamUrl: () => {},
    });
    await wait(900);
    handle.close();
    const afterClose = states.length;
    await wait(1500);
    try {
      expect(states[states.length - 1]).toBe("closed");
      expect(states.length).toBe(afterClose);
    } finally {
      fake.stop();
    }
  });
});

/**
 * The gap the age check cannot see.
 *
 * `stream.py` ends the response after `stale_after_s` without a frame — its
 * only in-band way to say "no longer live" — but `/status.live` is an age
 * comparison sampled at one instant. Polling at exactly the server's own
 * threshold meant a 1.0-2.0 s gap could close the connection while every poll
 * landed on a fresh frame either side of it.
 *
 * Nothing observed `live: false`, so nothing reopened, and the <img> held its
 * last frame for the rest of the session — at full brightness, with a green
 * badge, while the operator drove on a still photograph. Exactly the failure
 * this module's docstring says it exists to prevent.
 *
 * Asserted on the stream URLs handed out rather than on server hits: there is
 * no DOM here, so nothing actually fetches them. Handing out a new URL IS the
 * reconnect — the browser reuses a dead MJPEG response otherwise, so a retry
 * that does not change the URL is a retry that does nothing.
 */
test("a stall that never reports live:false still forces a reconnect", async () => {
  const fake = fakeVisionServer();
  const urls: string[] = [];
  try {
    const handle = connectRobotCamera(fake.url, {
      onState: () => {},
      onStatus: () => {},
      onStreamUrl: (url) => urls.push(url),
    });
    try {
      await Bun.sleep(700);
      const before = urls.length;
      expect(before).toBeGreaterThan(0);

      // Frames stop arriving, but `live` never goes false — the server keeps
      // reporting the last frame as recent enough at every sample.
      fake.freezeFrames(true);
      await Bun.sleep(1800);

      expect(urls.length).toBeGreaterThan(before);
      expect(urls[urls.length - 1]).not.toBe(urls[before - 1]);
    } finally {
      handle.close();
    }
  } finally {
    fake.stop();
  }
});

test("a healthy stream is never reconnected", async () => {
  // The counter check must not be trigger-happy: a needless reopen drops the
  // picture for a moment, and doing that to a working feed would be a new bug.
  const fake = fakeVisionServer();
  const urls: string[] = [];
  try {
    const handle = connectRobotCamera(fake.url, {
      onState: () => {},
      onStatus: () => {},
      onStreamUrl: (url) => urls.push(url),
    });
    try {
      await Bun.sleep(700);
      const before = urls.length;
      await Bun.sleep(2000);
      expect(urls.length).toBe(before);
    } finally {
      handle.close();
    }
  } finally {
    fake.stop();
  }
});
