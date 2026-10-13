<script lang="ts">
  // Shared frame for /login and /signup. Both pages used to render a stock
  // shadcn card on a bare background, in English — which made the front door
  // look like a different product from the console behind it.
  import type { Snippet } from "svelte";
  import { resolve } from "$app/paths";

  let {
    title,
    subtitle,
    children,
    footer,
    notices,
  }: {
    title: string;
    subtitle: string;
    children: Snippet;
    footer: Snippet;
    /** Persisting conditions (backend down, session expired) above the form. */
    notices?: Snippet;
  } = $props();
</script>

<!-- `ambient`: the same wash as the console shell (one utility in layout.css,
     not a copy), so the transition after sign-in is continuous rather than a
     jump cut. -->
<div
  class="relative flex min-h-svh items-center justify-center overflow-hidden bg-background ambient px-4 py-10 font-display"
>
  <main class="relative flex w-full max-w-sm flex-col items-center gap-7">
    <a
      href={resolve("/")}
      class="flex flex-col items-center gap-3 rounded-lg focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none"
    >
      <img
        src="/logo.svg"
        alt=""
        width="44"
        height="44"
        class="size-11 object-contain glow-cyan"
      />
      <span class="sr-only">C3PO — inicio</span>
    </a>

    <!-- The only panel on the page, so it takes the lift.

         The notices come FIRST in the markup and are drawn under the heading
         with `order`: a screen-reader user reading from the top meets "No se
         pudo contactar al servidor" before the form, not after tabbing into an
         "Ingresar" that cannot work. (Nothing in a notice takes focus, so the
         visual and reading orders never disagree for the keyboard.) -->
    <div class="flex w-full flex-col panel-hero p-7">
      {#if notices}
        <!-- `has-[*]:`: the snippet is always passed, often with nothing to
             say, and an empty wrapper must not add a gap above the form. -->
        <div class="flex flex-col gap-3 has-[*]:mb-5">
          {@render notices()}
        </div>
      {/if}

      <div class="order-first mb-6 flex flex-col gap-2 text-center">
        <h1 class="stamp text-xl text-ink">{title}</h1>
        <p class="text-sm text-ink-mute">{subtitle}</p>
      </div>

      {@render children()}
    </div>

    <p class="text-sm text-ink-mute">
      {@render footer()}
    </p>
  </main>
</div>
