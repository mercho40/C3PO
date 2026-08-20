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
declare module "bun:test" {
  export function describe(label: string, body: () => void): void;
  export function test(label: string, body: () => void | Promise<void>): void;

  interface Matchers {
    toBe(expected: unknown): void;
    toEqual(expected: unknown): void;
    toContainEqual(expected: unknown): void;
    toBeCloseTo(expected: number, precision?: number): void;
    toBeGreaterThan(expected: number): void;
    toBeGreaterThanOrEqual(expected: number): void;
    toBeLessThan(expected: number): void;
    toBeLessThanOrEqual(expected: number): void;
    toBeUndefined(): void;
    toThrow(expected?: unknown): void;
  }

  export function expect(actual: unknown): Matchers;
}
