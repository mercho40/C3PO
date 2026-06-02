<script lang="ts">
  let { data } = $props();
  let email = $state("");

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
      class="absolute bottom-10 left-1/2 -translate-x-1/2 text-muted-foreground/50 transition-colors hover:text-muted-foreground"
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
  <section id="como-funciona" class="relative overflow-hidden pt-32 pb-24">
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
      class="mt-16 flex items-center justify-center text-muted-foreground/50 transition-colors hover:text-muted-foreground"
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
  <footer class="border-t border-border bg-card px-6 py-12">
    <div class="mx-auto flex max-w-5xl items-center justify-between">
      <span class="text-lg font-bold">C3PO</span>
      <p class="text-sm text-muted-foreground">
        El puente entre la IA y la robótica
      </p>
    </div>
  </footer>
</div>
