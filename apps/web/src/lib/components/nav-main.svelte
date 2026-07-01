<script lang="ts">
  import type { Component } from "svelte";
  import { page } from "$app/state";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";

  type NavItem = {
    title: string;
    href?: string;
    icon: Component;
    disabled?: boolean;
    badge?: string;
  };

  let { label, items }: { label: string; items: NavItem[] } = $props();
</script>

<Sidebar.Group>
  <Sidebar.GroupLabel
    class="font-mono text-[9px] tracking-[0.22em] text-sidebar-foreground/55 uppercase"
  >
    {label}
  </Sidebar.GroupLabel>
  <Sidebar.Menu>
    {#each items as item (item.title)}
      {@const Icon = item.icon}
      {#if item.disabled}
        <Sidebar.MenuItem>
          <Sidebar.MenuButton
            aria-disabled="true"
            tabindex={-1}
            tooltipContent={item.title}
            class="cursor-default pe-14 text-sidebar-foreground/40 hover:bg-transparent hover:text-sidebar-foreground/40 aria-disabled:pointer-events-auto"
          >
            <Icon />
            <span>{item.title}</span>
          </Sidebar.MenuButton>
          {#if item.badge}
            <Sidebar.MenuBadge
              class="top-1.5 border border-sidebar-border text-[9px] font-medium tracking-wider text-sidebar-foreground/55 uppercase"
            >
              {item.badge}
            </Sidebar.MenuBadge>
          {/if}
        </Sidebar.MenuItem>
      {:else}
        {@const active = page.url.pathname === item.href}
        <Sidebar.MenuItem>
          <Sidebar.MenuButton isActive={active} tooltipContent={item.title}>
            {#snippet child({ props })}
              <a href={item.href} {...props}>
                <Icon />
                <span>{item.title}</span>
              </a>
            {/snippet}
          </Sidebar.MenuButton>
          {#if active}
            <!-- Straight cyan rail — an absolute bar avoids the button's rounded-corner curve. -->
            <span
              class="pointer-events-none absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-[#9ae5f8] shadow-[0_0_10px_rgba(126,229,255,0.65)] group-data-[collapsible=icon]:hidden"
            ></span>
          {/if}
        </Sidebar.MenuItem>
      {/if}
    {/each}
  </Sidebar.Menu>
</Sidebar.Group>
