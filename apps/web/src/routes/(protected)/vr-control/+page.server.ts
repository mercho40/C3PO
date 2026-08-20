import { redirect } from "@sveltejs/kit";
import type { PageServerLoad } from "./$types";
import { createApi } from "$lib/api";

/**
 * Seeds `env` (walk_velocity/dance are real-hardware-only — see
 * apps/bridge README's Phase 1b notes) and each preset skill's live
 * `works.real` flag from the catalogue, so the page can show an honest
 * "not yet live-tested" badge instead of silently hiding a capability —
 * same reasoning as live-camera's real-hardware guard and the bridge's own
 * `works_real` field (never claim something works before it's been watched
 * do so).
 */
export const load: PageServerLoad = async ({ fetch, request }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const [stateRes, skillsRes] = await Promise.all([
    api.state.get(),
    api.skills.get(),
  ]);

  if (stateRes.error && (stateRes.error.status as number) === 401)
    redirect(303, "/login");

  const worksReal: Record<string, boolean> = {};
  for (const skill of skillsRes.data?.skills ?? []) {
    worksReal[skill.name] = skill.works.real;
  }

  return {
    env: stateRes.error ? null : (stateRes.data?.env ?? null),
    worksReal,
  };
};
