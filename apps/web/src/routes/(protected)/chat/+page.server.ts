import type { PageServerLoad } from "./$types";
import { createApi } from "$lib/api";

/**
 * Chat history for the console.
 *
 * `?id=<chatId>` selects a conversation; without it the page starts a new one.
 * Both the list and the selected conversation are fetched server-side so the
 * first paint already has history — a chat that pops in after hydration reads
 * as a bug.
 *
 * The cookie header is forwarded explicitly: SvelteKit's server `fetch` does
 * not send cookies cross-origin (web :3001 → back :3000), so Better Auth
 * wouldn't see the session otherwise (see `$lib/api`).
 */
export const load: PageServerLoad = async ({ fetch, request, url }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const selectedId = url.searchParams.get("id");

  const [listResult, chatResult] = await Promise.all([
    api.chats.get({ query: {} }),
    selectedId ? api.chats({ id: selectedId }).get() : Promise.resolve(null),
  ]);

  // History is a convenience, not a precondition: if the list request fails the
  // operator should still be able to talk to the robot, just without a sidebar.
  const chats = listResult.error ? [] : (listResult.data?.chats ?? []);

  // A stale or foreign id 404s — fall back to a new chat rather than erroring
  // the page, since the id most likely came from a bookmark.
  const selected =
    chatResult && !chatResult.error ? chatResult.data : null;

  return {
    chats,
    selected: selected
      ? {
          id: selected.id,
          title: selected.title,
          messages: selected.messages.map((m) => ({
            id: m.id,
            role: m.role,
            // Stored verbatim as UIMessage.parts, so this rehydrates tool-call
            // cards exactly as they were streamed.
            parts: m.parts,
          })),
        }
      : null,
  };
};
