import { redirect } from "@sveltejs/kit";
import type { PageServerLoad } from "./$types";
import { createApi } from "#lib/api.js";
import type { SelectedStatus } from "#lib/chat/transcript.js";

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
 * wouldn't see the session otherwise (see `#lib/api`).
 */
export const load: PageServerLoad = async ({ fetch, request, url }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const selectedId = url.searchParams.get("id");

  const [listResult, chatResult] = await Promise.all([
    api.chats.get({ query: {} }),
    selectedId ? api.chats({ id: selectedId }).get() : Promise.resolve(null),
  ]);

  // A 401 is a session problem, never a server one. The layout only redirects
  // when /state says 401 — the cookie cache can outlive a revoked session by
  // up to 5 minutes, and then these were the first requests to find out. It
  // used to render "No se pudo cargar la conversación · El servidor no
  // respondió" and an empty rail marked as failed, with a Reintentar that
  // could only fail the same way. Back here afterwards, same conversation.
  if (
    (listResult.error?.status as number | undefined) === 401 ||
    (chatResult?.error?.status as number | undefined) === 401
  ) {
    const params = new URLSearchParams({
      redirectTo: url.pathname + url.search,
      reason: "expired",
    });
    redirect(303, `/login?${params}`);
  }

  // History is a convenience, not a precondition: if the list request fails the
  // operator should still be able to talk to the robot, just without a sidebar.
  // But a failed list is NOT an empty one — "Todavía no hay conversaciones"
  // over a backend that is down is a lie — so the failure travels with it.
  const historyError = Boolean(listResult.error);
  const chats = listResult.error ? [] : (listResult.data?.chats ?? []);

  // A stale or foreign id 404s (the backend scopes by user, so someone else's
  // chat is indistinguishable from a missing one). Either way the page still
  // opens a fresh chat — the id most likely came from a bookmark or an
  // incident note — but it says it couldn't open the one that was asked for,
  // instead of quietly showing a blank conversation as if that were it.
  let selectedStatus: SelectedStatus = "none";
  if (chatResult) {
    if (!chatResult.error) selectedStatus = "ok";
    else
      selectedStatus = chatResult.error.status === 404 ? "not-found" : "error";
  }
  const selected =
    chatResult && !chatResult.error ? (chatResult.data ?? null) : null;

  return {
    chats,
    historyError,
    selectedStatus,
    selected: selected
      ? {
          id: selected.id,
          title: selected.title,
          channel: selected.channel,
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
