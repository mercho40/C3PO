<script lang="ts">
  import * as Sheet from "#lib/components/ui/sheet/index.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import XIcon from "@lucide/svelte/icons/x";
  import { cn, type WithElementRef } from "#lib/utils.js";
  import type { HTMLAttributes } from "svelte/elements";
  import { SIDEBAR_WIDTH_MOBILE } from "./constants.js";
  import { useSidebar } from "./context.svelte.js";

  let {
    ref = $bindable(null),
    side = "left",
    variant = "sidebar",
    collapsible = "offcanvas",
    class: className,
    children,
    ...restProps
  }: WithElementRef<HTMLAttributes<HTMLDivElement>> & {
    side?: "left" | "right";
    variant?: "sidebar" | "floating" | "inset";
    collapsible?: "offcanvas" | "icon" | "none";
  } = $props();

  const sidebar = useSidebar();
</script>

{#if collapsible === "none"}
  <div
    class={cn(
      "flex h-full w-(--sidebar-width) flex-col bg-sidebar text-sidebar-foreground",
      className,
    )}
    bind:this={ref}
    {...restProps}
  >
    {@render children?.()}
  </div>
{:else if sidebar.isMobile}
  <Sheet.Root
    bind:open={() => sidebar.openMobile, (v) => sidebar.setOpenMobile(v)}
    {...restProps}
  >
    <!-- The stock close button is off (it was hidden with `[&>button]:hidden`,
         leaving an sr-only "Cerrar" with a 0×0 box — touch users had no
         visible way out but tapping the dimmed page). This header carries a
         real one instead, where the menu trigger was, so the tap that opened
         the sheet closes it again.

         The header is exactly as tall as the topbar's first row, and its right
         side is deliberately empty: the topbar's PARAR sits above every modal
         overlay (dashboard-topbar), so on a phone it floats over this corner of
         the sheet. It used to land on top of the brand row; now the brand row
         starts below it. -->
    <Sheet.Content
      bind:ref
      data-sidebar="sidebar"
      data-slot="sidebar"
      data-mobile="true"
      showCloseButton={false}
      class={cn(
        "w-(--sidebar-width) gap-0 bg-sidebar p-0 text-sidebar-foreground",
        className,
      )}
      style="--sidebar-width: {SIDEBAR_WIDTH_MOBILE};"
      {side}
    >
      <Sheet.Header
        class="flex h-14 shrink-0 flex-row items-center gap-2 px-3 py-0"
      >
        <Sheet.Close>
          {#snippet child({ props })}
            <Button
              variant="ghost"
              size="icon-sm"
              class="-ms-1 size-8 text-ink-mute hover:bg-accent hover:text-ink"
              {...props}
            >
              <XIcon aria-hidden="true" />
              <span class="sr-only">Cerrar el menú</span>
            </Button>
          {/snippet}
        </Sheet.Close>
        <Sheet.Title class="eyebrow text-2xs text-ink-mute">Menú</Sheet.Title>
        <Sheet.Description class="sr-only">
          Navegación de la consola
        </Sheet.Description>
      </Sheet.Header>
      <div class="flex h-full w-full flex-col">
        {@render children?.()}
      </div>
    </Sheet.Content>
  </Sheet.Root>
{:else}
  <div
    bind:this={ref}
    class="group peer hidden text-sidebar-foreground md:block"
    data-state={sidebar.state}
    data-collapsible={sidebar.state === "collapsed" ? collapsible : ""}
    data-variant={variant}
    data-side={side}
    data-slot="sidebar"
  >
    <!-- This is what handles the sidebar gap on desktop -->
    <div
      data-slot="sidebar-gap"
      class={cn(
        "relative w-(--sidebar-width) bg-transparent transition-[width] duration-200 ease-linear",
        "group-data-[collapsible=offcanvas]:w-0",
        "group-data-[side=right]:rotate-180",
        variant === "floating" || variant === "inset"
          ? "group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4)))]"
          : "group-data-[collapsible=icon]:w-(--sidebar-width-icon)",
      )}
    ></div>
    <div
      data-slot="sidebar-container"
      class={cn(
        "fixed inset-y-0 z-10 hidden h-svh w-(--sidebar-width) transition-[left,right,width] duration-200 ease-linear md:flex",
        side === "left"
          ? "start-0 group-data-[collapsible=offcanvas]:start-[calc(var(--sidebar-width)*-1)]"
          : "end-0 group-data-[collapsible=offcanvas]:end-[calc(var(--sidebar-width)*-1)]",
        // Adjust the padding for floating and inset variants.
        variant === "floating" || variant === "inset"
          ? "p-2 group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+(--spacing(4))+2px)]"
          : "group-data-[collapsible=icon]:w-(--sidebar-width-icon) group-data-[side=left]:border-e group-data-[side=right]:border-s",
        className,
      )}
      {...restProps}
    >
      <div
        data-sidebar="sidebar"
        data-slot="sidebar-inner"
        class="flex size-full flex-col bg-sidebar group-data-[variant=floating]:rounded-lg group-data-[variant=floating]:shadow-sm group-data-[variant=floating]:ring-1 group-data-[variant=floating]:ring-sidebar-border"
      >
        {@render children?.()}
      </div>
    </div>
  </div>
{/if}
