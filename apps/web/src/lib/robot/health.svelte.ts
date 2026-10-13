/**
 * The extra reads /health ("Salud") makes, beyond the console's shared pollers.
 *
 * The link, battery, faults and Nav2 gate rows read the layout's `RobotLive`
 * and `Gate` — polling /state a second time from this page would double the
 * MCP `get_state` load on the Jetson for the same answer. Everything else is
 * read here, in ONE poller (never two requests in flight, a timeout on all of
 * them, a 401 sends the operator to log in, paused while the tab is hidden):
 *
 *   GET  {API}/health                      public; server latency
 *   GET  {API}/skills                      catalogue envelope (throttled, below)
 *   GET  {API}/telemetry/surroundings      perception sources
 *   GET  {API}/telemetry/voice             the robot's microphone
 *   GET  {API}/map/costmap.png             only the X-C3PO-* headers matter
 *   GET  {PUBLIC_ROBOT_CAM_URL}/status     apps/back's relay (session cookie);
 *                                          no cookie if pointed at the robot
 *
 * Each read keeps its own outcome (see `Read` in `health.ts`), so one dead
 * source never blanks the others — the page exists to say WHICH part is down.
 *
 * /skills is throttled to every 15 s: GET /skills asks the bridge for its tool
 * list on every call (there is no TTL cache in back), and a diagnostics page
 * left open should not become a load source for the thing it diagnoses.
 *
 * Decision logic lives in `health.ts`; this file only fetches.
 */

import { PUBLIC_API_URL } from "$env/static/public";
import { Poller, type PollResult } from "./poller.svelte";
import { sameOrigin } from "./health";

import type {
  CameraStatus,
  CatalogueEnvelope,
  CostmapProbe,
  Read,
  ServerHealth,
  SurroundingsLite,
  VoiceTelemetry,
} from "./health";

const CATALOGUE_EVERY_MS = 15_000;
const COSTMAP_HEADERS = [
  "x-c3po-age-s",
  "x-c3po-stale",
  "x-c3po-width",
  "x-c3po-height",
  "x-c3po-resolution-m",
  "x-c3po-frame",
];

type Raw =
  | { kind: "ok"; res: Response; ms: number }
  | { kind: "network"; timeout: boolean };

async function request(
  url: string,
  signal: AbortSignal,
  credentials: RequestCredentials,
): Promise<Raw> {
  const started = performance.now();
  try {
    const res = await fetch(url, { credentials, signal, cache: "no-store" });
    return { kind: "ok", res, ms: performance.now() - started };
  } catch {
    return { kind: "network", timeout: signal.aborted };
  }
}

/** Turn one fetch into a `Read`, decoding the body with `decode` on 2xx. */
async function toRead<T>(
  raw: Raw,
  decode: (res: Response) => Promise<T>,
  headers?: string[],
): Promise<Read<T> & { unauthorized?: boolean }> {
  const at = Date.now();
  if (raw.kind === "network")
    return { state: "network", at, timeout: raw.timeout };
  const { res, ms } = raw;
  if (res.ok) {
    try {
      return {
        state: "ok",
        at,
        ms,
        status: res.status,
        data: await decode(res),
        ...(headers
          ? {
              headers: Object.fromEntries(
                headers.map((h) => [h, res.headers.get(h)]),
              ),
            }
          : {}),
      };
    } catch {
      // A 200 whose body is not what it should be is not a success.
      return {
        state: "http",
        at,
        ms,
        status: res.status,
        body: { error: "unreadable_body" },
      };
    }
  }
  const body = await res.json().catch(() => null);
  return {
    state: "http",
    at,
    ms,
    status: res.status,
    body,
    ...(res.status === 401 ? { unauthorized: true } : {}),
  };
}

export class HealthProbe extends Poller {
  server = $state.raw<Read<ServerHealth>>({ state: "idle" });
  catalogue = $state.raw<Read<CatalogueEnvelope>>({ state: "idle" });
  surroundings = $state.raw<Read<SurroundingsLite>>({ state: "idle" });
  voice = $state.raw<Read<VoiceTelemetry>>({ state: "idle" });
  costmap = $state.raw<Read<CostmapProbe>>({ state: "idle" });
  camera = $state.raw<Read<CameraStatus>>({ state: "idle" });

  readonly #cameraUrl: string;
  #catalogueAt = 0;

  /** @param cameraUrl `PUBLIC_ROBOT_CAM_URL`, trimmed; empty = not probed. */
  constructor(cameraUrl: string) {
    super({ timeoutMs: 4500, pauseWhenHidden: true });
    this.#cameraUrl = cameraUrl.replace(/\/+$/, "");
    if (!this.#cameraUrl) {
      this.camera = { state: "skipped", reason: "PUBLIC_ROBOT_CAM_URL vacío" };
    }
  }

  /** Re-read the catalogue on the next tick regardless of the throttle. */
  refresh(): void {
    this.#catalogueAt = 0;
    void this.tick();
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    const api = PUBLIC_API_URL;
    const wantCatalogue = Date.now() - this.#catalogueAt >= CATALOGUE_EVERY_MS;

    const [server, catalogue, surroundings, voice, costmap, camera] =
      await Promise.all([
        request(`${api}/health`, signal, "omit").then((r) =>
          toRead<ServerHealth>(r, (res) => res.json()),
        ),
        wantCatalogue
          ? request(`${api}/skills`, signal, "include").then((r) =>
              toRead<CatalogueEnvelope>(r, async (res) => {
                // Only the envelope; the skills array is large and unused here.
                const { count, source, age_seconds, bridge_error, rejected } =
                  (await res.json()) as CatalogueEnvelope;
                return { count, source, age_seconds, bridge_error, rejected };
              }),
            )
          : null,
        request(`${api}/telemetry/surroundings`, signal, "include").then((r) =>
          toRead<SurroundingsLite>(r, (res) => res.json()),
        ),
        request(`${api}/telemetry/voice`, signal, "include").then((r) =>
          toRead<VoiceTelemetry>(r, (res) => res.json()),
        ),
        request(`${api}/map/costmap.png`, signal, "include").then((r) =>
          toRead<CostmapProbe>(
            r,
            async (res) => ({ bytes: (await res.arrayBuffer()).byteLength }),
            COSTMAP_HEADERS,
          ),
        ),
        // Normally apps/back's relay, behind the login like everything above:
        // without the cookie every probe was a 401, and this page reported a
        // working camera as down. A debug URL aimed straight at the robot
        // gets no cookie — the bridge grants no credentialed CORS.
        this.#cameraUrl
          ? request(
              `${this.#cameraUrl}/status`,
              signal,
              sameOrigin(this.#cameraUrl, api) ? "include" : "omit",
            ).then((r) => toRead<CameraStatus>(r, (res) => res.json()))
          : null,
      ]);

    // A 401 from any protected read is a session problem, never a robot
    // problem — the base class sends the operator to log in.
    const reads = [catalogue, surroundings, voice, costmap, camera];
    if (reads.some((r) => r && "unauthorized" in r && r.unauthorized)) {
      return "unauthorized";
    }

    this.server = server;
    if (catalogue) {
      this.catalogue = catalogue;
      // Only a real answer resets the throttle, so a failed read retries
      // on the next tick instead of waiting 15 s to find out again.
      if (catalogue.state === "ok") this.#catalogueAt = Date.now();
    }
    this.surroundings = surroundings;
    this.voice = voice;
    this.costmap = costmap;
    if (camera) this.camera = camera;

    return server.state === "ok" ? "ok" : "error";
  }
}
