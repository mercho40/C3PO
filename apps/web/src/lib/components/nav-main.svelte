<script lang="ts">
  import { navigating, page } from "$app/state";
  import { resolve } from "$app/paths";
  import type { Pathname } from "$app/types";
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import { useSidebar } from "$lib/components/ui/sidebar/index.js";
  import { isActive, type NavGroup } from "$lib/nav";

  let { group }: { group: NavGroup } = $props();

  const sidebar = useSidebar();

  // Where a client navigation is heading. The target's server load can wait on
  // the bridge (a /vr-control load awaits /state and the catalogue), and until
  // it resolves the old page stays up with the rail on the OLD item — so a tap
  // during a bridge stall looked dead and got tapped again.
  const pendingPath = $derived(navigating.to?.url.pathname ?? null);
</script>

<Sidebar.Group>
  <!-- `eyebrow` and nothing else. This carried `font-mono`, which made the two
       sidebar kickers the only eyebrows in the app set in Plex Mono while the
       other twelve were Archivo — same label role, two families, both visible
       in one frame. The explicit `text-ink-mute` beats GroupLabel's own
       `text-sidebar-foreground/70` base class, which tailwind-merge cannot see
       conflicts with a custom utility. -->
  <Sidebar.GroupLabel class="eyebrow text-ink-mute">
    {group.label}
  </Sidebar.GroupLabel>
  <Sidebar.Menu>
    {#each group.items as item (item.href)}
      {@const Icon = item.icon}
      <!-- `isActive` matches nested routes too (/chat ⊃ /chat/x); the old exact
           `pathname === href` lost the rail on any child page. -->
      {@const active = isActive(page.url.pathname, item.href)}
      {@const pending =
        !active && pendingPath !== null && isActive(pendingPath, item.href)}
      <Sidebar.MenuItem>
        <!-- `onclick` goes through MenuButton so it is merged with the
             tooltip trigger's own handlers rather than replacing them. -->
        <Sidebar.MenuButton
          isActive={active}
          tooltipContent={item.title}
          onclick={() => {
            // The phone nav is a modal sheet; leaving it open after a tap kept
            // its overlay over the page — and over the e-stop — so the first
            // tap on PARAR after any navigation only dismissed it. app-sidebar
            // also closes it after every navigation, whatever started it.
            if (sidebar.isMobile) sidebar.setOpenMobile(false);
          }}
        >
          {#snippet child({ props })}
            <a
              href={resolve(item.href as Pathname)}
              {...props}
              aria-current={active ? "page" : undefined}
              data-pending={pending || undefined}
            >
              <Icon />
              <span>{item.title}</span>
            </a>
          {/snippet}
        </Sidebar.MenuButton>
        {#if active || pending}
          <!-- Straight rail — an absolute bar avoids the button's rounded-corner
               curve. Periwinkle, never cyan: cyan means "moving under power"
               and nothing else, and the rail used to be the brightest cyan on
               every page while the robot sat still. Active is solid with a
               halo; pending is the same hue, dimmer and pulsing, on the item
               being loaded. -->
          <span
            aria-hidden="true"
            class={[
              "pointer-events-none absolute inset-y-1.5 left-0 w-0.5 rounded-full group-data-[collapsible=icon]:hidden",
              active
                ? "bg-peri shadow-(--c3-glow-rail)"
                : "bg-peri/70 motion-safe:animate-pulse",
            ]}
          ></span>
        {/if}
      </Sidebar.MenuItem>
    {/each}
  </Sidebar.Menu>
</Sidebar.Group>
