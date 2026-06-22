<script lang="ts">
  import { page } from "$app/state";
  import { Search, Bell } from "@lucide/svelte";

  let { placeholder = "Buscar…" }: { placeholder?: string } = $props();
  let query = $state("");

  const titles: Record<string, string> = {
    "/live-map": "Mapa en vivo",
    "/live-camera": "Cámaras",
    "/chat": "Chat",
  };
  const title = $derived(titles[page.url.pathname] ?? "");
</script>

<header class="flex h-[55px] items-center justify-between gap-2.5 px-1">
  <h1 class="font-display text-[28px] font-medium tracking-[-0.02em] text-[#eaf1ff]">
    {title}
  </h1>
  <div class="flex items-center gap-2.5">
    <div
      class="flex h-9 w-52 items-center gap-2.5 rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] px-3.5"
    >
      <Search class="size-3.5 shrink-0 text-[#8a96ad]" />
      <input
        bind:value={query}
        {placeholder}
        class="font-mono h-full w-full bg-transparent text-[12px] text-[#eaf1ff] placeholder:text-[#8a96ad] focus:outline-none"
      />
    </div>
    <button
      aria-label="Notificaciones"
      class="relative flex size-9 items-center justify-center rounded-full border border-[rgba(180,210,255,0.08)] bg-[rgba(180,210,255,0.03)] text-[#8a96ad] transition-colors hover:text-[#eaf1ff]"
    >
      <Bell class="size-3.5" />
      <span
        class="absolute top-1.5 right-1.5 size-1.5 rounded-sm bg-[#ff4d6a] shadow-[0px_0px_8px_rgba(255,77,106,0.7)]"
      ></span>
    </button>
  </div>
</header>
