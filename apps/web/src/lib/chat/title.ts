/**
 * Chat titles. The backend derives one from the first prompt (db/chats.ts
 * `titleFromParts`, 80 chars) and `PATCH /chats/:id` accepts 1–120 chars.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

export const TITLE_MAX = 120;

export const UNTITLED = "Sin título";

/** What an operator typed, as the backend will accept it — or null if empty. */
export function normalizeTitle(input: string): string | null {
  const t = input.replace(/\s+/g, " ").trim();
  if (!t) return null;
  return t.length > TITLE_MAX ? t.slice(0, TITLE_MAX) : t;
}

export function titleOf(
  row: { title: string | null } | null | undefined,
): string {
  return row?.title?.trim() || UNTITLED;
}
