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
  <!-- `eyebrow` and nothing else. This carried `font-mono`, which made the two
       sidebar kickers the only eyebrows in the app set in Plex Mono while the
       other twelve were Archivo — same label role, two families, both visible
       in one frame. The explicit `text-ink-mute` beats GroupLabel's own
       `text-sidebar-foreground/70` base class, which tailwind-merge cannot see
       conflicts with a custom utility. -->
  <Sidebar.GroupLabel class="eyebrow text-ink-mute">
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
              class="top-1.5 rounded-full border border-sidebar-border px-1.5 text-2xs font-medium tracking-wider text-sidebar-foreground/60 uppercase"
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
              class="pointer-events-none absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-cyan shadow-[0_0_10px_rgba(126,229,255,0.65)] group-data-[collapsible=icon]:hidden"
            ></span>
          {/if}
        </Sidebar.MenuItem>
      {/if}
    {/each}
  </Sidebar.Menu>
</Sidebar.Group>
