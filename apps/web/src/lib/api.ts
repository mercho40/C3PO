import { treaty } from "@elysiajs/eden";
import type { App } from "@back/index";
import { PUBLIC_API_URL } from "$env/static/public";

export const createApi = (fetch: typeof globalThis.fetch) =>
  treaty<App>(PUBLIC_API_URL, {
    fetch: { credentials: "include" },
    fetcher: fetch,
  });
