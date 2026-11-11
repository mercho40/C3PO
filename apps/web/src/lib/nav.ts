/**
 * The console's routes, once.
 *
 * The sidebar and the topbar each kept their own list of pages, and they drifted:
 * /vr-control was in the sidebar and missing from the topbar's title map, so the
 * topbar rendered an empty <h1> there. Every console page now gets its sidebar
 * entry, its topbar title and its browser-tab title from this file.
 */

import type { Component } from "svelte";
import {
  Activity,
  Gamepad2,
  HeartPulse,
  Map,
  MessageSquare,
  Mic,
  ScanFace,
  Settings,
  Video,
} from "@lucide/svelte";

export type NavItem = {
  title: string;
  href: string;
  icon: Component;
  /** Listed only for admins; back refuses everyone else anyway. */
  adminOnly?: true;
};

export type NavGroup = {
  label: string;
  items: NavItem[];
};

export const NAV: NavGroup[] = [
  {
    label: "Plataforma",
    items: [
      { title: "Inicio", href: "/dashboard", icon: Activity },
      { title: "Mapa en vivo", href: "/live-map", icon: Map },
      { title: "Cámaras", href: "/live-camera", icon: Video },
      { title: "Chat", href: "/chat", icon: MessageSquare },
      { title: "Control por voz", href: "/voice", icon: Mic },
      { title: "Control VR", href: "/vr-control", icon: Gamepad2 },
    ],
  },
  {
    label: "Sistema",
    items: [
      { title: "Salud", href: "/health", icon: HeartPulse },
      { title: "Caras", href: "/faces", icon: ScanFace, adminOnly: true },
      { title: "Configuración", href: "/settings", icon: Settings },
    ],
  },
];

/** The sidebar for this session: admin-only pages hidden from operators. */
export function navFor(isAdmin: boolean): NavGroup[] {
  return NAV.map((g) => ({
    ...g,
    items: g.items.filter((i) => isAdmin || !i.adminOnly),
  }));
}

const ALL: NavItem[] = NAV.flatMap((g) => g.items);

/** Whether `href` is the current page or an ancestor of it (`/chat` ⊃ `/chat/x`). */
export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The nav entry for a path, if it is a console page. */
export function navItemFor(pathname: string): NavItem | undefined {
  return ALL.find((item) => isActive(pathname, item.href));
}

/** The title for a path — the topbar heading and the browser tab. */
export function titleFor(pathname: string): string {
  return navItemFor(pathname)?.title ?? "";
}
