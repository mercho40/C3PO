<script lang="ts">
  import { page } from "$app/state";
  import { Bell } from "@lucide/svelte";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu/index.js";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import { Separator } from "$lib/components/ui/separator/index.js";

  const titles: Record<string, string> = {
    "/dashboard": "Inicio",
    "/live-map": "Mapa en vivo",
    "/live-camera": "Cámaras",
    "/chat": "Chat",
  };
  const title = $derived(titles[page.url.pathname] ?? "");
</script>

<header class="flex h-[55px] items-center gap-2.5 px-1">
  <Sidebar.Trigger
    class="-ms-1 size-8 text-[#8a96ad] hover:bg-[rgba(159,197,255,0.07)] hover:text-[#eaf1ff]"
  />
  <Separator
    orientation="vertical"
    class="me-1 h-5 bg-[rgba(180,210,255,0.12)]"
  />
  <h1 class="font-display text-[28px] font-medium tracking-[-0.02em] text-[#eaf1ff]">
    {title}
  </h1>
  <div class="ms-auto flex items-center gap-2.5">
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label="Notificaciones"
        class="relative flex size-9 items-center justify-center rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] text-[#8a96ad] transition-colors hover:text-[#eaf1ff]"
      >
        <Bell class="size-3.5" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="end" class="w-64">
        <DropdownMenu.Label>Notificaciones</DropdownMenu.Label>
        <DropdownMenu.Separator />
        <div class="px-2 py-6 text-center text-xs text-muted-foreground">
          Sin notificaciones nuevas
        </div>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  </div>
</header>
