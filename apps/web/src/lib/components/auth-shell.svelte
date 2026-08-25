<script lang="ts">
  // Shared frame for /login and /signup. Both pages used to render a stock
  // shadcn card on a bare background, in English — which made the front door
  // look like a different product from the console behind it.
  import type { Snippet } from "svelte";

  let {
    title,
    subtitle,
    children,
    footer,
  }: {
    title: string;
    subtitle: string;
    children: Snippet;
    footer: Snippet;
  } = $props();
</script>

<div
  class="relative flex min-h-svh items-center justify-center overflow-hidden bg-background px-4 py-10 font-display"
>
  <!-- Same ambient wash as the console shell, so the transition after sign-in
       is continuous rather than a jump cut. -->
  <div
    class="pointer-events-none absolute inset-0"
    style="background:
      radial-gradient(80% 50% at 50% 0%, rgba(159,197,255,0.12), rgba(159,197,255,0) 65%),
      radial-gradient(50% 40% at 90% 100%, rgba(74,125,209,0.12), rgba(74,125,209,0) 70%);"
  ></div>

  <div class="relative z-10 flex w-full max-w-sm flex-col items-center gap-7">
    <a href="/" class="flex flex-col items-center gap-3">
      <img src="/logo.svg" alt="" class="size-11 object-contain glow-cyan" />
      <span class="sr-only">C3PO — inicio</span>
    </a>

    <!-- The only panel on the page, so it takes the lift. -->
    <div class="w-full panel-hero p-7">
      <div class="mb-6 flex flex-col gap-2 text-center">
        <h1 class="stamp text-xl text-ink">{title}</h1>
        <p class="text-sm text-ink-mute">{subtitle}</p>
      </div>

      {@render children()}
    </div>

    <p class="text-sm text-ink-mute">
      {@render footer()}
    </p>
  </div>
</div>
