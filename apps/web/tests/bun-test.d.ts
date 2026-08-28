/**
 * Minimal ambient declaration for `bun:test`.
 *
 * `bun test` supplies these at runtime, but svelte-check does not know them —
 * SvelteKit's generated tsconfig includes `tests/**` in the project, so a test
 * importing from "bun:test" fails the app's own type check.
 *
 * The obvious fix is adding `bun-types` to `compilerOptions.types`. Do not:
 * that package globally redefines `fetch` with Bun's signature, and
 * `chat/+page.svelte` passes SvelteKit's `fetch` where `typeof fetch` is
 * expected — so the whole app stops type-checking to satisfy one test file.
 *
 * This shim is deliberately small. It covers what these tests use and nothing
 * else; widen it when a test needs more, rather than reaching for the global
 * package again.
 */
/** Minimal `Bun.serve` surface, for tests that stand up a real HTTP server. */
declare const Bun: {
  serve(options: {
    port?: number;
    fetch: (req: Request) => Response | Promise<Response>;
  }): { port: number; stop(closeActiveConnections?: boolean): void };
  /** Promise-based sleep, for tests that have to let real timers elapse. */
  sleep(ms: number): Promise<void>;
};

declare module "bun:test" {
  export function describe(label: string, body: () => void): void;
  export function test(label: string, body: () => void | Promise<void>): void;

  interface Matchers {
    toBe(expected: unknown): void;
    toEqual(expected: unknown): void;
    toContainEqual(expected: unknown): void;
    toBeCloseTo(expected: number, precision?: number): void;
    toBeNull(): void;
    toBeGreaterThan(expected: number): void;
    toBeGreaterThanOrEqual(expected: number): void;
    toBeLessThan(expected: number): void;
    toBeLessThanOrEqual(expected: number): void;
    toBeUndefined(): void;
    toBeDefined(): void;
    toContain(expected: unknown): void;
    toThrow(expected?: unknown): void;
    /** Negation. Only the matchers actually used are declared. */
    not: Omit<Matchers, "not">;
  }

  export function expect(actual: unknown): Matchers;
}
