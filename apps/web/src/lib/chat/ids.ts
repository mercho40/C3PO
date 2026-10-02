/**
 * Client-minted ids for the chat (draft conversation ids, attachment ids).
 *
 * `crypto.randomUUID` exists only in a SECURE context — https, or localhost.
 * Operators open this console on phones, tablets and the Quest at
 * `http://<lan-ip>:3001`, where it is `undefined`, and calling it while the
 * /chat page initialised threw during hydration: the chat simply did not work
 * on any device but the laptop running the dev server.
 *
 * `crypto.getRandomValues` has no such restriction, so the fallback builds the
 * same RFC 4122 version-4 shape from it — the backend stores ids as text, but a
 * draft id minted on a phone should look like one minted on the laptop.
 *
 * Plain module, no `$` imports, so `bun test` can load it.
 */

type CryptoLike = {
  getRandomValues(array: Uint8Array<ArrayBuffer>): Uint8Array<ArrayBuffer>;
  randomUUID?: () => string;
};

export function newId(c: CryptoLike = globalThis.crypto): string {
  if (typeof c.randomUUID === "function") return c.randomUUID();
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // RFC 4122 variant
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
