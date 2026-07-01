import { redirect } from "@sveltejs/kit";
import { SIDEBAR_COOKIE_NAME } from "$lib/components/ui/sidebar/constants.js";
import type { LayoutServerLoad } from "./$types";

export const load: LayoutServerLoad = ({ locals, cookies }) => {
  if (!locals.user) redirect(303, "/login");

  // Persist the sidebar's expanded/collapsed state across reloads (the
  // SidebarProvider writes this cookie; we read it back here for SSR).
  const sidebarState = cookies.get(SIDEBAR_COOKIE_NAME);
  return { sidebarOpen: sidebarState === undefined ? true : sidebarState === "true" };
};
