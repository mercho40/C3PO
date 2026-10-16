import { redirect } from "@sveltejs/kit";
import type { PageServerLoad } from "./$types";
import { createApi } from "#lib/api.js";
import {
  GESTURE_CATALOGUE_FALLBACK,
  catalogueHealth,
  gestureNamesFromSchema,
} from "#lib/robot/vr-logic.js";

/**
 * Seeds each preset skill's live `works.real` flag from the catalogue, so the
 * page can show an honest "not yet live-tested" badge instead of silently
 * hiding a capability — same reasoning as the bridge's own `works_real` field
 * (never claim something works before it's been watched do so). walk_velocity
 * and dance are the two this was written for.
 *
 * NO /state CALL. This load used to fetch /state a second time to learn `env`,
 * which the (protected) layout already fetches — and that copy was frozen at
 * page load: open the page while the bridge was still coming up and the "not
 * in real mode" banner and the missing camera stayed until a reload, in a
 * headset. The page now reads the bridge's mode from the shared `RobotLive`
 * poller, seeded by the layout.
 *
 * BOUNDED. GET /skills reaches the bridge over MCP, whose default timeout is
 * 60 s. A sidebar tap on "Control VR" during a bridge stall must not leave the
 * old page on screen for a minute; past the timeout the catalogue is simply
 * unknown, and the page says so.
 */
export const load: PageServerLoad = async ({ fetch, request, url }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const res = await api.skills
    .get({ fetch: { signal: AbortSignal.timeout(2500) } })
    .catch(() => ({ data: null, error: { status: 504, value: null } }));

  if (res.error && (res.error.status as number) === 401) {
    const params = new URLSearchParams({
      redirectTo: url.pathname + url.search,
      reason: "expired",
    });
    redirect(303, `/login?${params}`);
  }

  const body = res.error ? null : res.data;
  const skills = body?.skills ?? [];
  const worksReal: Record<string, boolean> = {};
  // Catalogue danger levels decide which tiles need a hold to confirm
  // (`needsHoldConfirm`): the bridge owns that judgement, not this page.
  const dangerLevels: Record<string, string> = {};
  for (const skill of skills) {
    worksReal[skill.name] = skill.works.real;
    dangerLevels[skill.name] = skill.dangerLevel;
  }

  // An empty catalogue makes `worksReal[name] === false` false for EVERY
  // skill, so all the "Sin probar en real" badges used to quietly disappear —
  // the page looking MORE confident about the robot's capabilities at the
  // moment it knew least. And apps/back never reports that as an HTTP error:
  // with the bridge down it answers 200 with `skills: []` and `bridge_error`.
  // `catalogueHealth` reads the envelope, not the status code.
  const catalogue = catalogueHealth({
    httpFailed: Boolean(res.error),
    bridgeError: body?.bridge_error ?? null,
    skillCount: skills.length,
    source: body?.source ?? null,
    ageSeconds: body?.age_seconds ?? null,
  });

  const gestureSkill = skills.find((s) => s.name === "gesture");
  return {
    worksReal,
    dangerLevels,
    catalogue,
    // The bridge's own list when its schema declares one; otherwise the mirror
    // in vr-logic.ts (whose comment names the source of truth).
    gestureNames: gestureNamesFromSchema(gestureSkill?.parameters) ?? [
      ...GESTURE_CATALOGUE_FALLBACK,
    ],
    // null = unknown (no catalogue), so the page doesn't claim the tool is
    // missing when it simply couldn't look.
    gestureTool: catalogue.failed ? null : Boolean(gestureSkill),
  };
};
