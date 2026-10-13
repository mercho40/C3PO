/**
 * /camera/* — the robot's camera, proxied from the bridge.
 *
 * WHY THIS EXISTS: THE BROWSER USED TO READ THE CAMERA FROM THE BRIDGE ITSELF.
 *
 * `PUBLIC_ROBOT_CAM_URL` pointed at `127.0.0.1:8001/camera`, and 8001 is the
 * bridge — which also serves `/mcp`, the tool surface that can walk the robot,
 * with no authentication of its own. So `scripts/quest_setup.sh` forwarded that
 * port into the headset's browser, and the bridge had to grant CORS on
 * `/camera/*` for the picture to load at all. It was not exploitable only
 * because a test kept the CORS header off every other route.
 *
 * Proxying here puts the camera where `/telemetry/*` and `/map/costmap.png`
 * already are: behind Better Auth, on 3000, which the headset already has. The
 * browser never needs a route to the bridge's port again.
 *
 * The bridge still picks the source (videohub or the vision container — see
 * `bridge/sdk/camera_relay.py`); this only relays whatever it serves.
 *
 * WHY THE STREAM IS A DIFFERENT SHAPE FROM EVERY OTHER PROXY IN THIS APP.
 *
 * `/stream.mjpg` is `multipart/x-mixed-replace` and never ends while the feed is
 * live, so two things that are harmless elsewhere are wrong here:
 *
 *   * A whole-request `AbortSignal.timeout` would cut a healthy stream after N
 *     seconds. The timeout covers only the wait for response HEADERS.
 *   * The upstream request must end when the viewer goes away. `mjpeg-camera.ts`
 *     reopens with a new URL on every stall, and each reopen would otherwise
 *     leave the previous stream running — frames nobody displays, through the
 *     SSH tunnel, for the life of `back`. The client's own abort signal is
 *     chained to the upstream fetch.
 */

import { Elysia } from "elysia";

import { bridgeSiblingUrl } from "@back/bridge/url";

/** How long to wait for the bridge to START answering. Not a stream limit. */
export const CONNECT_TIMEOUT_MS = 3000;

type StatusFn = (code: number, body: unknown) => unknown;

/**
 * 503 is the bridge saying "no feed, and here is why" — pass it through with
 * its body, so the console can name the reason instead of drawing black.
 * Anything else that is not OK is flattened to 502.
 */
async function upstreamFailure(
  upstream: Response,
  status: StatusFn,
): Promise<unknown | null> {
  if (upstream.status === 503) {
    return status(
      503,
      await upstream.json().catch(() => ({ error: "no_camera" })),
    );
  }
  if (!upstream.ok) return status(502, { error: "bridge_error" });
  return null;
}

async function proxyStatus(status: StatusFn): Promise<unknown> {
  let upstream: Response;
  try {
    upstream = await fetch(bridgeSiblingUrl("/camera/status"), {
      // Polled twice a second; a slow answer is a stale answer.
      signal: AbortSignal.timeout(CONNECT_TIMEOUT_MS),
    });
  } catch {
    return status(502, { error: "bridge_unavailable" });
  }
  const failed = await upstreamFailure(upstream, status);
  if (failed !== null) return failed;

  return new Response(await upstream.arrayBuffer(), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}

async function proxyStream(
  request: Request,
  status: StatusFn,
): Promise<unknown> {
  const upstreamAbort = new AbortController();
  const onClientGone = () => upstreamAbort.abort();
  if (request.signal.aborted) upstreamAbort.abort();
  request.signal.addEventListener("abort", onClientGone, { once: true });

  const connectTimer = setTimeout(
    () => upstreamAbort.abort(),
    CONNECT_TIMEOUT_MS,
  );
  let upstream: Response;
  try {
    upstream = await fetch(bridgeSiblingUrl("/camera/stream.mjpg"), {
      signal: upstreamAbort.signal,
    });
  } catch {
    request.signal.removeEventListener("abort", onClientGone);
    return status(502, { error: "bridge_unavailable" });
  } finally {
    // Headers are in (or it failed): from here the stream runs as long as the
    // viewer stays, and only the viewer leaving ends it.
    clearTimeout(connectTimer);
  }

  const failed = await upstreamFailure(upstream, status);
  if (failed !== null) {
    request.signal.removeEventListener("abort", onClientGone);
    return failed;
  }
  if (!upstream.body) {
    request.signal.removeEventListener("abort", onClientGone);
    return status(502, { error: "bridge_error" });
  }

  return new Response(upstream.body, {
    headers: {
      // Carries the multipart boundary; the <img> cannot split frames without it.
      "Content-Type":
        upstream.headers.get("Content-Type") ??
        "multipart/x-mixed-replace; boundary=c3poframe",
      "Cache-Control": "no-store",
    },
  });
}

export const cameraRoutes = new Elysia()
  .get("/camera/status", ({ status }) => proxyStatus(status), {
    detail: {
      summary:
        "Whether the robot camera is live, which source is serving, and why not.",
      tags: ["camera"],
    },
  })
  .get(
    "/camera/stream.mjpg",
    ({ request, status }) => proxyStream(request, status),
    {
      detail: {
        summary:
          "The robot camera as MJPEG. Closing the request closes the upstream stream.",
        tags: ["camera"],
      },
    },
  );
