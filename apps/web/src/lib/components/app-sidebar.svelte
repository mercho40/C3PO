<script lang="ts">
  import type { ComponentProps } from "svelte";
  import {
    Activity,
    HeartPulse,
    Map,
    MessageSquare,
    Mic,
    Settings,
    Video,
  } from "@lucide/svelte";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import NavMain from "./nav-main.svelte";
  import NavUser from "./nav-user.svelte";

  let { ref = $bindable(null), ...restProps }: ComponentProps<typeof Sidebar.Root> = $props();

  const navMain = [
    { title: "Inicio", href: "/dashboard", icon: Activity },
    { title: "Mapa en vivo", href: "/live-map", icon: Map },
    { title: "Cámaras", href: "/live-camera", icon: Video },
    { title: "Chat", href: "/chat", icon: MessageSquare },
    // Not built yet — shown but disabled so they don't 404.
    { title: "Control por voz", icon: Mic, disabled: true, badge: "Pronto" },
    { title: "Salud", icon: HeartPulse, disabled: true, badge: "Pronto" },
  ];

  const navSystem = [{ title: "Configuración", icon: Settings, disabled: true, badge: "Pronto" }];
</script>

<Sidebar.Root bind:ref collapsible="icon" {...restProps}>
  <Sidebar.Header>
    <Sidebar.Menu>
      <Sidebar.MenuItem>
        <Sidebar.MenuButton size="lg">
          {#snippet child({ props })}
            <a href="/dashboard" {...props}>
              <div
                class="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary/10 ring-1 ring-sidebar-border"
              >
                <img
                  src="/logo.svg"
                  alt=""
                  class="size-5 object-contain drop-shadow-[0_0_8px_rgba(126,229,255,0.55)]"
                />
              </div>
              <div class="grid flex-1 text-start text-sm leading-tight">
                <span class="truncate font-semibold tracking-tight text-[#eaf1ff]">C3PO</span>
                <span class="truncate text-xs text-sidebar-foreground/55">Consola de operador</span>
              </div>
            </a>
          {/snippet}
        </Sidebar.MenuButton>
      </Sidebar.MenuItem>
    </Sidebar.Menu>
  </Sidebar.Header>

  <Sidebar.Content>
    <NavMain label="Plataforma" items={navMain} />
    <NavMain label="Sistema" items={navSystem} />
  </Sidebar.Content>

  <Sidebar.Footer>
    <NavUser />
  </Sidebar.Footer>

  <Sidebar.Rail />
</Sidebar.Root>
