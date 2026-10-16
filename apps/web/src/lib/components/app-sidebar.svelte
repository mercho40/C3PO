<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { afterNavigate } from "$app/navigation";
  import { resolve } from "$app/paths";
  import * as Sidebar from "#lib/components/ui/sidebar/index.js";
  import { useSidebar } from "#lib/components/ui/sidebar/index.js";
  import { NAV } from "#lib/nav.js";
  import NavMain from "./nav-main.svelte";
  import NavUser from "./nav-user.svelte";

  let {
    ref = $bindable(null),
    ...restProps
  }: ComponentProps<typeof Sidebar.Root> = $props();

  const sidebar = useSidebar();

  // Every page the sidebar lists now exists, so there are no disabled "Pronto"
  // entries left. "Control por voz" said "Pronto" while voice control shipped
  // on the dashboard — an absence that read as "not built yet".

  // Belt and braces with nav-main's onclick: whatever navigated (a link inside
  // the sheet, the logo, the browser's back button), the phone's modal sheet
  // must not stay over the page — or over the e-stop.
  // Shallow `goto`s (/chat rewriting its own query) are not the operator
  // picking a page, so they leave the sheet alone.
  afterNavigate(({ shallow }) => {
    if (!shallow) sidebar.setOpenMobile(false);
  });
</script>

<Sidebar.Root bind:ref collapsible="icon" {...restProps}>
  <Sidebar.Header>
    <Sidebar.Menu>
      <Sidebar.MenuItem>
        <Sidebar.MenuButton size="lg">
          {#snippet child({ props })}
            <a href={resolve("dashboard")} {...props}>
              <div
                class="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary/10 ring-1 ring-sidebar-border"
              >
                <img
                  src="/logo.svg"
                  alt=""
                  width="20"
                  height="20"
                  class="size-5 object-contain glow-cyan"
                />
              </div>
              <div class="grid flex-1 text-start text-sm leading-tight">
                <span class="truncate font-semibold tracking-tight text-ink"
                  >C3PO</span
                >
                <span class="truncate text-xs text-sidebar-foreground/60"
                  >Consola de operador</span
                >
              </div>
            </a>
          {/snippet}
        </Sidebar.MenuButton>
      </Sidebar.MenuItem>
    </Sidebar.Menu>
  </Sidebar.Header>

  <Sidebar.Content>
    {#each NAV as group (group.label)}
      <NavMain {group} />
    {/each}
  </Sidebar.Content>

  <Sidebar.Footer>
    <NavUser />
  </Sidebar.Footer>

  <Sidebar.Rail />
</Sidebar.Root>
