/**
 * The USB link watch on /vr-control.
 *
 * `adb reverse` forwards live on the USB connection, not the headset. Unplug
 * the cable, jostle it, or let the device drop its ADB session and ALL of
 * them vanish at once — and nothing recreates them. From inside the headset
 * that is indistinguishable from the server being down, which is exactly how
 * it presented on 2026-08-20: "localhost is not working", with every service
 * on the Mac perfectly healthy.
 *
 * Polling our own origin is enough to tell them apart, because the page was
 * served through the same forward that would have died.
 *
 * WHAT is polled matters. This used to be `HEAD /vr-control?ping`, and
 * SvelteKit renders pages for HEAD: every 5 s, from every open tab, it ran the
 * auth hook, the (protected) layout load (GET /state → the bridge), this
 * page's load (GET /skills → the bridge) and a full SSR. A bridge or tunnel
 * stall longer than the 4 s abort then read as "se perdió el enlace USB… el
 * robot está bien" — confidently blaming a cable for a robot-side problem.
 *
 * A static file is served before SvelteKit's handler (by Vite's asset
 * middleware in dev, by the CDN in production): no hook, no load, no backend.
 * It answers if and only if the path to the console server is up, which is
 * exactly the question. Any HTTP status counts as "up" — `fetch` only rejects
 * when nothing answered at all.
 *
 * NOT a `Poller` (`poller.svelte.ts`), on purpose: a 401 is impossible here
 * (no route, no auth) and "any status is up" is not that policy's reading of a
 * response; and the first probe goes out one period after mount, not at once.
 * The 4 s timeout under a 5 s period is what keeps two probes from overlapping.
 *
 * Moved out of `vr-control.svelte.ts` intact.
 */

const LINK_PROBE_MS = 5000;
const LINK_PROBE_TIMEOUT_MS = 4000;

export class LinkWatch {
  linkLost = $state(false);

  #linkTimer: ReturnType<typeof setInterval> | null = null;

  start(): void {
    if (this.#linkTimer) return;
    this.#linkTimer = setInterval(async () => {
      try {
        await fetch(`/robots.txt?ping=${Date.now()}`, {
          method: "HEAD",
          cache: "no-store",
          signal: AbortSignal.timeout(LINK_PROBE_TIMEOUT_MS),
        });
        this.linkLost = false;
      } catch {
        this.linkLost = true;
      }
    }, LINK_PROBE_MS);
  }

  stop(): void {
    if (this.#linkTimer) clearInterval(this.#linkTimer);
    this.#linkTimer = null;
  }
}
