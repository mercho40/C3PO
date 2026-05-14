import { getCookieCache } from "better-auth/cookies";
import type { Handle } from "@sveltejs/kit";

export const handle: Handle = async ({ event, resolve }) => {
  const session = await getCookieCache(event.request);
  event.locals.user = session?.user ?? null;

  return resolve(event, {
    preload: ({ type }) => type === "font" || type === "js" || type === "css",
  });
};
