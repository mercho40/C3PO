<script lang="ts">
  import { page } from "$app/state";
  import { Search, Bell } from "@lucide/svelte";
  import { Input } from "$lib/components/ui/input/index.js";
  import * as DropdownMenu from "$lib/components/ui/dropdown-menu/index.js";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import { Separator } from "$lib/components/ui/separator/index.js";

  let { placeholder = "Buscar…" }: { placeholder?: string } = $props();
  let query = $state("");

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
    <div
      class="flex h-9 w-52 items-center gap-2.5 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] px-3.5"
    >
      <Search class="size-3.5 shrink-0 text-[#8a96ad]" />
      <Input
        bind:value={query}
        {placeholder}
        class="font-mono h-full w-full rounded-none border-0 bg-transparent p-0 text-[12px] text-[#eaf1ff] shadow-none placeholder:text-[#8a96ad] focus-visible:ring-0 dark:bg-transparent"
      />
    </div>
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label="Notificaciones"
        class="relative flex size-9 items-center justify-center rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] text-[#8a96ad] transition-colors hover:text-[#eaf1ff]"
      >
        <Bell class="size-3.5" />
        <span
          class="absolute top-1.5 right-1.5 size-1.5 rounded-sm bg-[#ff4d6a] shadow-[0px_0px_8px_rgba(255,77,106,0.7)]"
        ></span>
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
