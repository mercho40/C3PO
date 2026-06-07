<script lang="ts">
  import { onMount } from "svelte";

  let { data } = $props();
  let email = $state("");

  let heroSection: HTMLElement;
  let comoFuncionaSection: HTMLElement;
  let heroArrowVisible = $state(true);
  let comoFuncionaArrowVisible = $state(true);

  onMount(() => {
    const onScroll = () => {
      const y = window.scrollY;
      heroArrowVisible = y < (heroSection?.offsetHeight ?? Infinity) * 0.4;
      comoFuncionaArrowVisible =
        y < (heroSection?.offsetHeight ?? 0) + (comoFuncionaSection?.offsetHeight ?? Infinity) * 0.4;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  });

  function handleWaitlist(e: SubmitEvent) {
    e.preventDefault();
  }
</script>

{#snippet badge(label: string)}
  <div class="relative z-10 flex justify-center">
    <span
      class="rounded-full border border-border bg-card px-4 py-1.5 text-xs font-medium whitespace-nowrap text-muted-foreground"
    >
      {label}
    </span>
  </div>
{/snippet}

{#snippet stepCard(title: string, description: string, highlight = false)}
  <div
    class="flex items-center gap-4 rounded-xl border bg-card p-5 {highlight
      ? 'border-primary/40 ring-1 ring-primary/20'
      : 'border-border'}"
  >
    <div class="min-w-0 flex-1">
      <h3 class="mb-2 text-sm font-semibold">{title}</h3>
      <p class="text-xs leading-relaxed text-muted-foreground">{description}</p>
    </div>
    <div class="size-14 shrink-0 rounded-lg bg-muted"></div>
  </div>
{/snippet}

{#snippet featureLarge(title: string, description: string)}
  <div
    class="flex items-center gap-6 rounded-xl border border-border bg-card p-6"
  >
    <div class="flex-1">
      <h3 class="mb-2 font-semibold">{title}</h3>
      <p class="text-sm leading-relaxed text-muted-foreground">{description}</p>
    </div>
    <div class="size-24 shrink-0 rounded-xl bg-muted"></div>
  </div>
{/snippet}

{#snippet featureSmall(title: string, description: string)}
  <div class="rounded-xl border border-border bg-card p-5">
    <div class="mb-4 h-28 w-full rounded-lg bg-muted"></div>
    <h3 class="mb-1.5 text-sm font-semibold">{title}</h3>
    <p class="text-xs leading-relaxed text-muted-foreground">{description}</p>
  </div>
{/snippet}

<div class="dark min-h-screen bg-background text-foreground">
  <!-- Navbar -->
  <nav
    class="fixed top-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-4 rounded-full border border-border bg-card/80 px-3 py-2 backdrop-blur-sm"
  >
    <img src="/logo.svg" alt="C3PO" class="h-7 w-auto object-contain" />
    <div class="flex items-center gap-5 text-sm text-muted-foreground">
      <a href="#como-funciona" class="transition-colors hover:text-foreground"
        >¿Cómo funciona?</a
      >
      <a href="#features" class="transition-colors hover:text-foreground"
        >Features</a
      >
      <a href="/chat" class="transition-colors hover:text-foreground">Chat</a>
    </div>
    <a
      href={data.user ? "/dashboard" : "/login"}
      class="rounded-full bg-primary px-4 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
    >
      {data.user ? "Dashboard" : "Get Started"}
    </a>
  </nav>

  <!-- Hero -->
  <section
    bind:this={heroSection}
    class="relative flex min-h-screen flex-col items-center justify-center px-6 pb-20 text-center"
  >
    <h1 class="text-[9rem] leading-none font-bold tracking-tight">C3PO</h1>
    <p class="mt-4 max-w-sm text-base text-muted-foreground">
      El puente entre la IA y la robótica
    </p>
    <form
      class="mt-10 flex w-full max-w-sm items-center gap-2"
      onsubmit={handleWaitlist}
    >
      <input
        type="email"
        bind:value={email}
        placeholder="Tu e-mail"
        required
        class="h-10 flex-1 rounded-lg border border-border bg-input/30 px-4 text-sm placeholder:text-muted-foreground focus:ring-2 focus:ring-ring focus:outline-none"
      />
      <button
        type="submit"
        class="h-10 shrink-0 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        Get Started
      </button>
    </form>
    <a
      href="#como-funciona"
      aria-label="Ir a ¿Cómo funciona?"
      class="absolute bottom-10 left-1/2 -translate-x-1/2 text-muted-foreground/50 transition-all duration-500 hover:text-muted-foreground {heroArrowVisible ? 'opacity-100' : 'opacity-0 pointer-events-none'}"
    >
      <svg
        class="size-4 animate-bounce"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        stroke-width="2"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M19 9l-7 7-7-7"
        />
      </svg>
    </a>
  </section>

  <!-- How it works -->
  <section bind:this={comoFuncionaSection} id="como-funciona" class="relative overflow-hidden pt-32 pb-24">
    <div
      class="absolute -top-48 left-1/2 -z-10 h-[900px] w-[900px] -translate-x-1/2 rounded-full bg-muted/20"
    ></div>
    <div class="mx-auto max-w-5xl px-6">
      <div class="mb-20 text-center">
        <h2 class="text-4xl font-bold">¿Cómo Funciona?</h2>
        <p class="mx-auto mt-3 max-w-sm text-muted-foreground">
          Controlá el robot en tres pasos usando lenguaje natural
        </p>
      </div>

      <div class="relative">
        <div
          class="absolute top-0 bottom-0 left-1/2 w-px -translate-x-1/2 bg-border"
        ></div>

        <div
          class="relative mb-14 grid grid-cols-[1fr_120px_1fr] items-center gap-6"
        >
          <div></div>
          {@render badge("Paso 1")}
          {@render stepCard(
            "Enviá un comando",
            'Escribís o hablás en lenguaje natural: "caminá hacia la puerta"',
          )}
        </div>

        <div
          class="relative mb-14 grid grid-cols-[1fr_120px_1fr] items-center gap-6"
        >
          {@render stepCard(
            "La IA lo interpreta",
            "Claude procesa tu intención y genera un plan de acción estructurado",
            true,
          )}
          {@render badge("Paso 2")}
          <div></div>
        </div>

        <div class="relative grid grid-cols-[1fr_120px_1fr] items-center gap-6">
          <div></div>
          {@render badge("Paso 3")}
          {@render stepCard(
            "El robot lo ejecuta",
            "El Unitree G1 realiza el movimiento en tiempo real con supervisión completa",
          )}
        </div>
      </div>
    </div>
    <a
      href="#features"
      aria-label="Ir a Features"
      class="mt-16 flex items-center justify-center text-muted-foreground/50 transition-all duration-500 hover:text-muted-foreground {comoFuncionaArrowVisible ? 'opacity-100' : 'opacity-0 pointer-events-none'}"
    >
      <svg
        class="size-4 animate-bounce"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        stroke-width="2"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M19 9l-7 7-7-7"
        />
      </svg>
    </a>
  </section>

  <!-- Features -->
  <section id="features" class="py-32">
    <div class="mx-auto max-w-5xl px-6">
      <div class="mb-16 text-center">
        <h2 class="text-4xl font-bold">Features</h2>
        <p class="mx-auto mt-3 max-w-sm text-muted-foreground">
          Todo lo que necesitás para controlar un robot humanoide
        </p>
      </div>

      <div class="mb-4 grid grid-cols-2 gap-4">
        {@render featureLarge(
          "Lenguaje Natural",
          "Controlá el robot con palabras. Sin código ni interfaces técnicas.",
        )}
        {@render featureLarge(
          "Potenciado por Claude",
          "La IA de Anthropic interpreta y planifica cada acción del robot.",
        )}
      </div>

      <div class="grid grid-cols-3 gap-4">
        {@render featureSmall(
          "Robot Humanoide G1",
          "Hardware Unitree G1 de última generación con locomoción avanzada.",
        )}
        {@render featureSmall(
          "Simulación Segura",
          "Probá en Isaac Sim antes de ejecutar en hardware real.",
        )}
        {@render featureSmall(
          "Supervisión Web",
          "Panel de control en tiempo real desde cualquier navegador.",
        )}
      </div>
    </div>
  </section>

  <!-- Footer -->
  <footer class="border-t border-border bg-card px-6 pt-16 pb-8">
    <div class="mx-auto max-w-5xl">

      <!-- Top grid -->
      <div class="grid grid-cols-2 gap-12 md:grid-cols-4">

        <!-- Brand -->
        <div class="col-span-2 md:col-span-1">
          <div class="mb-4 flex items-center gap-2">
            <img src="/logo.svg" alt="C3PO" class="h-7 w-auto object-contain" />
          </div>
          <p class="mb-6 max-w-xs text-sm leading-relaxed text-muted-foreground">
            Controlá cualquier robot Unitree con lenguaje natural. Sin código, sin ingenieros de robótica.
          </p>
          <!-- Social -->
          <div class="flex items-center gap-3">
            <a
              href="https://linkedin.com"
              aria-label="LinkedIn"
              class="flex size-8 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
            >
              <svg class="size-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
              </svg>
            </a>
            <a
              href="https://instagram.com"
              aria-label="Instagram"
              class="flex size-8 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
            >
              <svg class="size-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/>
              </svg>
            </a>
            <a
              href="https://x.com"
              aria-label="X / Twitter"
              class="flex size-8 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
            >
              <svg class="size-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.744l7.737-8.835L1.254 2.25H8.08l4.253 5.622 5.91-5.622zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
              </svg>
            </a>
          </div>
        </div>

        <!-- Producto -->
        <div>
          <h4 class="mb-4 text-xs font-semibold uppercase tracking-widest text-foreground">Producto</h4>
          <ul class="space-y-3 text-sm text-muted-foreground">
            <li><a href="#como-funciona" class="transition-colors hover:text-foreground">¿Cómo funciona?</a></li>
            <li><a href="#features" class="transition-colors hover:text-foreground">Features</a></li>
            <li><a href="/chat" class="transition-colors hover:text-foreground">Chat</a></li>
            <li><a href="/dashboard" class="transition-colors hover:text-foreground">Dashboard</a></li>
          </ul>
        </div>

        <!-- Hardware -->
        <div>
          <h4 class="mb-4 text-xs font-semibold uppercase tracking-widest text-foreground">Hardware</h4>
          <ul class="space-y-3 text-sm text-muted-foreground">
            <li><span>Unitree G1</span></li>
            <li><span>Unitree Go2</span></li>
            <li><span>Isaac Sim</span></li>
          </ul>
        </div>

        <!-- Tecnología -->
        <div>
          <h4 class="mb-4 text-xs font-semibold uppercase tracking-widest text-foreground">Tecnología</h4>
          <ul class="space-y-3 text-sm text-muted-foreground">
            <li>
              <a href="https://anthropic.com" target="_blank" rel="noopener noreferrer" class="transition-colors hover:text-foreground">
                Claude AI · Anthropic
              </a>
            </li>
            <li><span>Unitree SDK2</span></li>
            <li><span>DDS / WebRTC</span></li>
            <li><span>SvelteKit · Elysia</span></li>
          </ul>
        </div>
      </div>

      <!-- Divider -->
      <div class="mt-12 border-t border-border pt-6 flex flex-col items-center justify-between gap-3 sm:flex-row">
        <p class="text-xs text-muted-foreground">
          © 2026 C3PO. Proyecto Final · Ingeniería.
        </p>
        <p class="text-xs text-muted-foreground">
          Hecho con Claude · Buenos Aires, Argentina
        </p>
      </div>

    </div>
  </footer>
</div>
