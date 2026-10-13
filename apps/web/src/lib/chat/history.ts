/**
 * /chat's bookkeeping decisions: when the history rail may offer "Ver más",
 * and when a draft conversation's id goes into the URL.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

/** The backend lists 50 by default and at most 200. */
export const HISTORY_PAGE = 50;
export const HISTORY_MAX = 200;

/**
 * Whether more conversations may exist beyond the ones listed.
 *
 * Decided from what the last FETCH returned, not from the rows on screen. It
 * used to compare the rail's current length with the limit, so deleting one
 * chat from a full page of 50 left 49 rows — "fewer than asked for, so that
 * is all of them" — and "Ver más" vanished with 150 conversations still
 * unlisted.
 */
export function canLoadMoreHistory(opts: {
  historyError: boolean;
  /** How many rows the last list request returned. */
  fetched: number;
  /** The limit that request asked for. */
  limit: number;
}): boolean {
  return (
    !opts.historyError && opts.fetched >= opts.limit && opts.limit < HISTORY_MAX
  );
}

/**
 * Whether to write the draft's id into the URL now.
 *
 * Once, when a brand-new conversation (no `?id=` loaded) has had its first
 * turn ACCEPTED by the backend — a reply streaming or already there. Not on
 * the first message alone: with back down that send fails, no row is ever
 * written, and the URL would advertise a conversation that does not exist
 * (a reload then said "No encontramos esa conversación").
 *
 * And never again for the same draft: "Nuevo chat" navigates to plain /chat,
 * and between that navigation landing and `afterNavigate` minting the next
 * draft id, the OLD conversation is still the one on screen, with messages.
 * Without `pinned` the pin ran in that window and wrote the previous
 * conversation's `?id=` back over the new, empty chat — a reload then
 * reopened the old one.
 */
export function shouldPinDraft(opts: {
  /** The draft's id already went into the URL (it names a server row). */
  pinned: boolean;
  messageCount: number;
  /** A conversation was loaded from `?id=` — its id is already in the URL. */
  hasSelected: boolean;
  /** The backend has answered this conversation (streaming, or a reply). */
  accepted: boolean;
}): boolean {
  return (
    !opts.pinned && !opts.hasSelected && opts.accepted && opts.messageCount > 0
  );
}
