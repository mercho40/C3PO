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

  const navItems = [
    { label: "Inicio", href: "/dashboard", icon: Activity },
    { label: "Mapa en vivo", href: "/live-map", icon: Map },
    { label: "Cámaras", href: "/live-camera", icon: Video },
    { label: "Chat", href: "/chat", icon: MessageSquare },
    { label: "Control por voz", href: "/voice-control", icon: Mic },
    { label: "Salud", href: "/health", icon: HeartPulse },
  ];

  const user = $derived(page.data.user);
  const displayName = $derived(user?.name ?? user?.email ?? "Perfil");
  const initials = $derived(
    (displayName as string)
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
  </nav>

  <div class="mt-5 px-2">
    <span
      class="font-mono text-[9px] tracking-[0.22em] text-[#8a96ad]/70 uppercase"
      >Sistema</span
    >
  </div>
  <nav class="flex flex-col gap-1">
    <a
      href="/settings"
      class="flex items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-[13px] text-[#8a96ad] transition-colors hover:bg-[rgba(159,197,255,0.06)] hover:text-[#c6dcff]"
    >
      <Settings class="size-3.5 shrink-0" />
      <span>Configuración</span>
    </a>
  </nav>

  <a
    href="/settings"
    class="mt-auto flex items-center gap-2.5 rounded-[10px] border border-[rgba(180,210,255,0.08)] bg-[#0c1220] p-3.5 transition-colors hover:border-[rgba(159,197,255,0.25)]"
  >
    <span
      class="flex size-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#9fc5ff] to-[#4a7dd1] font-mono text-[11px] font-bold text-[#06121c]"
      >{initials}</span
    >
    <span class="truncate text-xs text-[#eaf1ff]">{displayName}</span>
  </a>
</aside>
