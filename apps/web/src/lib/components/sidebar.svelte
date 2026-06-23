<script lang="ts">
  import {
    Activity,
    Map,
    Video,
    MessageSquare,
    Mic,
    HeartPulse,
    Settings,
  } from "@lucide/svelte";
  import { page } from "$app/state";
  import * as Avatar from "$lib/components/ui/avatar/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Separator } from "$lib/components/ui/separator/index.js";

  const navItems = [
    { label: "Inicio", href: "/dashboard", icon: Activity },
    { label: "Mapa en vivo", href: "/live-map", icon: Map },
    { label: "Cámaras", href: "/live-camera", icon: Video },
    { label: "Chat", href: "/chat", icon: MessageSquare },
  ];

  // Routes not built yet — shown but disabled so they don't 404.
  const pendingItems = [
    { label: "Control por voz", icon: Mic },
    { label: "Salud", icon: HeartPulse },
  ];

  const user = $derived(page.data.user);
  const displayName = $derived(String(user?.name ?? user?.email ?? "Perfil"));
  const initials = $derived(
    displayName
      .split(/\s+/)
      .map((part) => part[0] ?? "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "C3",
  );
</script>

<aside
  class="flex h-full w-60 flex-col gap-2 border-r border-[rgba(180,210,255,0.08)] bg-[rgba(5,7,13,0.6)] px-6 py-6 backdrop-blur-sm"
>
  <a href="/dashboard" class="mb-3 inline-flex w-fit">
    <img
      src="/logo.svg"
      alt="C3PO"
      class="h-9 w-auto object-contain drop-shadow-[0_0_12px_rgba(126,229,255,0.45)]"
    />
  </a>

  <nav class="flex flex-col gap-1">
    {#each navItems as item (item.href)}
      {@const Icon = item.icon}
      {@const active = page.url.pathname === item.href}
      <a
        href={item.href}
        class="relative flex items-center gap-3 rounded-lg border px-3 py-2.5 text-[13px] transition-colors {active
          ? 'border-[rgba(159,197,255,0.25)] bg-[rgba(159,197,255,0.14)] text-[#c6dcff]'
          : 'border-transparent text-[#8a96ad] hover:bg-[rgba(159,197,255,0.06)] hover:text-[#c6dcff]'}"
      >
        {#if active}
          <span
            class="absolute top-2 left-[-1px] h-5 w-0.5 rounded-sm bg-[#9ae5f8] shadow-[0px_0px_12px_rgba(126,229,255,0.55)]"
          ></span>
        {/if}
        <Icon class="size-3.5 shrink-0" />
        <span>{item.label}</span>
      </a>
    {/each}
    {#each pendingItems as item (item.label)}
      {@const Icon = item.icon}
      <span
        aria-disabled="true"
        class="relative flex cursor-not-allowed items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-[13px] text-[#8a96ad]/40"
      >
        <Icon class="size-3.5 shrink-0" />
        <span>{item.label}</span>
        <Badge
          variant="outline"
          class="ml-auto border-[rgba(180,210,255,0.18)] px-1.5 py-0 text-[9px] tracking-wide text-[#8a96ad] uppercase"
          >Pronto</Badge
        >
      </span>
    {/each}
  </nav>

  <Separator class="my-2 bg-[rgba(180,210,255,0.08)]" />

  <div class="px-2">
    <span class="font-mono text-[9px] tracking-[0.22em] text-[#8a96ad]/70 uppercase"
      >Sistema</span
    >
  </div>
  <span
    aria-disabled="true"
    class="flex cursor-not-allowed items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-[13px] text-[#8a96ad]/40"
  >
    <Settings class="size-3.5 shrink-0" />
    <span>Configuración</span>
    <Badge
      variant="outline"
      class="ml-auto border-[rgba(180,210,255,0.18)] px-1.5 py-0 text-[9px] tracking-wide text-[#8a96ad] uppercase"
      >Pronto</Badge
    >
  </span>

  <div
    class="mt-auto flex items-center gap-2.5 rounded-[10px] border border-[rgba(180,210,255,0.08)] bg-[#0c1220] p-3.5"
  >
    <Avatar.Root class="size-7 shrink-0">
      <Avatar.Fallback
        class="bg-gradient-to-br from-[#9fc5ff] to-[#4a7dd1] font-mono text-[11px] font-bold text-[#06121c]"
        >{initials}</Avatar.Fallback
      >
    </Avatar.Root>
    <span class="truncate text-xs text-[#eaf1ff]">{displayName}</span>
  </div>
</aside>
