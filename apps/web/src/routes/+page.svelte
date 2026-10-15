<script lang="ts">
  /*
   * The public landing page.
   *
   * It used to be a different product from the console behind it: 38 lines of
   * raw hex/rgba (including the exact card recipe layout.css says `panel`
   * replaced, and a grey that exists nowhere in the palette), English mixed with
   * Spanish and tuteo with voseo, and claims the system cannot back — GPS, Wi-Fi
   * pairing "sin tocar código", "cualquier robot Unitree", "sub-50ms en
   * cualquier parte" — plus placeholder social links and a "Contactanos" that
   * scrolled to nothing. The console's honest-state rule (never show what is not
   * true) applies to the pitch too: everything below is something that ships.
   */
  import type { Component } from "svelte";
  import {
    Activity,
    ArrowDown,
    ArrowRight,
    Gamepad2,
    Mic,
    Navigation,
    OctagonX,
    Video,
  } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { refreshAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { authClient } from "#lib/auth-client.js";

  const REPO = "https://github.com/mercho40/C3PO";

  let { data } = $props();
  let loggingOut = $state(false);

  // Same contract as the sidebar's logout (nav-user): a failure is SAID. This
  // one used to swallow both kinds — the button flickered "Cerrando…" back to
  // "Cerrar sesión" and nothing told the operator their session was still open.
  async function logout() {
    if (loggingOut) return;
    loggingOut = true;
    try {
      const { error } = await authClient.signOut();
      if (error) {
        toast.error("No se pudo cerrar la sesión", {
          description: "El servidor rechazó el pedido. Probá de nuevo.",
        });
        return;
      }
      // Re-run the layout load so data.user clears and the nav flips back.
      await refreshAll();
    } catch {
      toast.error("No se pudo cerrar la sesión", {
        description:
          "No se pudo contactar al servidor. Tu sesión sigue abierta.",
      });
    } finally {
      loggingOut = false;
    }
  }

  let heroHeight = $state(Infinity);
  let howHeight = $state(Infinity);
  let scrollY = $state(0);
  const heroArrowVisible = $derived(scrollY < heroHeight * 0.4);
  const howArrowVisible = $derived(scrollY < heroHeight + howHeight * 0.4);
  // The fixed nav's right-hand links had no backing, so once the page scrolled
  // they floated straight over the cards (and on a phone they are the only nav).
  const scrolled = $derived(scrollY > 8);

  type Step = {
    title: string;
    body: string;
    img: string;
    alt: string;
    width: number;
    height: number;
    /** A screenshot drawn on its own dark ground: fill the frame (cover) and
     *  fade its edges into the card, with the label it carries far too small
     *  to read repeated underneath at a size that can be. */
    screen?: { caption: string };
  };
  const STEPS: Step[] = [
    {
      title: "Conectá el robot",
      body: "El puente de C3PO corre a bordo del G1 y habla con el robot por su red interna. Vos abrís la consola en el navegador, desde la compu, la tablet o el visor.",
      img: "/landing/robot-standing.webp",
      alt: "Un robot humanoide Unitree G1 de pie",
      width: 1024,
      height: 643,
    },
    {
      title: "Seguí todo desde tu pantalla",
      body: "Mirá la cámara del robot, su posición en el mapa (odometría), la batería, la postura y los fallos — y cada dato se marca como desactualizado en cuanto deja de llegar.",
      img: "/landing/chart.webp",
      alt: "Mapa de la consola con el recorrido del robot BIPED-01",
      width: 560,
      height: 712,
      screen: { caption: "BIPED-01 · recorrido por odometría" },
    },
    {
      title: "Indicá con voz o chat",
      body: "Hablale en español o escribile órdenes en lenguaje natural. Un agente las traduce en habilidades del robot, y PARAR queda siempre a un toque.",
      img: "/landing/robot-hand.webp",
      alt: "Brazo y mano del robot Unitree G1",
      width: 1024,
      height: 643,
    },
  ];

  type Feature = { title: string; body: string; icon: Component };
  const FEATURES: Feature[] = [
    {
      title: "Comandos por voz",
      // Scoped to the agent on purpose. This said "nunca queda escuchando de
      // fondo", which is false: the bridge keeps a background listener on the
      // robot's microphone at all times (it transcribes and spots the stop
      // phrase). What is true is that the AGENT only acts on speech while
      // someone has started a voice session.
      body: "Conversá con el robot en español. Mientras hay una sesión de voz iniciada, lo que escucha se vuelve un turno de conversación y los pedidos físicos claros disparan sus habilidades. Sin una sesión iniciada, el agente no actúa por lo que oye.",
      icon: Mic,
    },
    {
      title: "Telemetría en vivo",
      body: "Batería, postura, fallos y estado del enlace, con cada lectura marcada como desactualizada cuando el robot deja de responder.",
      icon: Activity,
    },
    {
      title: "Cámara del robot",
      body: "La transmisión de la cámara del G1 en el navegador, junto con lo que su percepción detecta alrededor.",
      icon: Video,
    },
    {
      title: "Navegación autónoma",
      body: "El agente puede armar la navegación con Nav2 por un tiempo limitado, y la consola muestra en todo momento si está armada y cuánto le queda.",
      icon: Navigation,
    },
    {
      title: "Parada de emergencia",
      body: "PARAR está en cada pantalla — también dentro del visor de VR, y con Shift+Espacio — y solo dice «Detenido» cuando la telemetría lo confirma.",
      icon: OctagonX,
    },
    {
      title: "Control VR",
      body: "Teleoperación desde el navegador del Meta Quest 3: marcha, gestos y espejo de brazos.",
      icon: Gamepad2,
    },
  ];

  const year = new Date().getFullYear();
</script>

<svelte:head>
  <title>C3PO · Consola para el Unitree G1</title>
  <meta
    name="description"
    content="C3PO es una consola web para operar un robot humanoide Unitree G1 con voz o chat en lenguaje natural, telemetría en vivo y parada de emergencia en cada pantalla."
  />
</svelte:head>

<svelte:window bind:scrollY />

{#snippet scrollArrow(
  target: string,
  label: string,
  visible: boolean,
  extra = "",
)}
  <!-- Hidden arrows used to stay in the tab order, so Tab landed on invisible
       links; and the bounce ignored reduced motion. -->
  <a
    href={`#${target}`}
    aria-label={label}
    aria-hidden={!visible}
    tabindex={visible ? 0 : -1}
    class={[
      "rounded-full p-1 text-ink-mute/60 transition-opacity duration-500 hover:text-ink-mute focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none",
      extra,
      visible ? "opacity-100" : "pointer-events-none opacity-0",
    ]}
  >
    <ArrowDown class="size-4 motion-safe:animate-bounce" aria-hidden="true" />
  </a>
{/snippet}

<!-- `svh`, not `screen` (100vh): on a phone 100vh is the height with the URL
     bar hidden, so the hero's centred content sat under the browser chrome. -->
<div
  class="relative min-h-svh overflow-x-hidden bg-abyss font-display text-ink"
>
  <!-- z-0, not negative: the root is not a stacking context, so a negative
       layer is painted underneath its own background and the glow vanished. -->
  <div
    class="pointer-events-none absolute inset-x-0 top-0 z-0 h-[1200px] ambient-hero"
    aria-hidden="true"
  ></div>

  <!-- Navbar -->
  <nav
    aria-label="Principal"
    class="fixed inset-x-0 top-4 z-50 mx-auto flex max-w-[1461px] items-center justify-between px-4 sm:px-6 md:grid md:grid-cols-[1fr_auto_1fr]"
  >
    <!-- Flex below md: the centre pill is hidden there, and a hidden grid item
         gives up its cell, so the right-hand links used to slide into the
         middle column instead of sitting at the edge. -->
    <!-- Scrolled, the logo gets the same opaque backing as the links on the
         right; it used to float bare over the cards' text. -->
    <a
      href={resolve("/")}
      class={[
        "justify-self-start rounded-full border p-1 transition-colors duration-300 focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none",
        scrolled
          ? "border-hairline-strong bg-panel/95 backdrop-blur-md"
          : "border-transparent",
      ]}
    >
      <img
        src="/logo.svg"
        alt="C3PO — inicio"
        width="31"
        height="36"
        class="h-9 w-auto object-contain glow-cyan"
      />
    </a>
    <!-- A neutral shadow, not `glow-soft`: that cyan halo is the `cta`'s,
         and cyan means "moving under power". Near-opaque, so scrolled text
         does not read through it (85 % plus blur still let it). -->
    <div
      class="hidden items-center gap-10 rounded-full border border-hairline-strong bg-panel/95 px-12 py-3.5 text-sm font-medium text-ink-dim shadow-(--c3-shadow-float) backdrop-blur-md md:flex"
    >
      <a
        href="#como-funciona"
        class="transition-colors hover:text-ink focus-visible:text-ink focus-visible:underline focus-visible:outline-none"
        >¿Cómo funciona?</a
      >
      <a
        href="#caracteristicas"
        class="transition-colors hover:text-ink focus-visible:text-ink focus-visible:underline focus-visible:outline-none"
        >Características</a
      >
      <a
        href={REPO}
        class="transition-colors hover:text-ink focus-visible:text-ink focus-visible:underline focus-visible:outline-none"
        >Código</a
      >
    </div>
    <div
      class={[
        "flex items-center gap-1.5 justify-self-end rounded-full border p-1 transition-colors duration-300 sm:gap-2",
        scrolled
          ? "border-hairline-strong bg-panel/95 shadow-(--c3-shadow-float) backdrop-blur-md"
          : "border-transparent",
      ]}
    >
      {#if data.user}
        <button
          type="button"
          onclick={logout}
          disabled={loggingOut}
          class="rounded-full px-3 py-2 text-sm text-ink-dim transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none disabled:opacity-50 sm:px-4"
        >
          {loggingOut ? "Cerrando…" : "Cerrar sesión"}
        </button>
      {:else}
        <a
          href={resolve("login")}
          class="rounded-full px-3 py-2 text-sm text-ink-dim transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-cyan-hot/50 focus-visible:outline-none sm:px-4"
        >
          Ingresar
        </a>
      {/if}
      <a
        href={resolve(data.user ? "dashboard" : "signup")}
        class="flex items-center gap-2 rounded-full cta px-4 py-2 text-sm font-medium focus-visible:ring-2 focus-visible:ring-cyan-hot/60 focus-visible:outline-none sm:px-5"
      >
        {data.user ? "Ir a la consola" : "Crear cuenta"}
        <ArrowRight class="size-3.5" aria-hidden="true" />
      </a>
    </div>
  </nav>

  <main>
    <!-- Hero -->
    <section
      bind:offsetHeight={heroHeight}
      aria-labelledby="hero-title"
      class="relative flex min-h-svh flex-col items-center justify-center px-6 pt-24 pb-20 text-center"
    >
      <h1
        id="hero-title"
        class="stamp text-7xl leading-none text-ink sm:text-[116px]"
      >
        C3PO
      </h1>
      <p class="mt-6 max-w-[660px] text-lg leading-relaxed text-ink-mute">
        Una consola web para operar un robot humanoide Unitree G1: hablale o
        escribile en lenguaje natural, seguí su telemetría en vivo y tené la
        parada de emergencia siempre a mano.
      </p>

      {#if data.user}
        <a
          href={resolve("dashboard")}
          class="mt-10 flex items-center gap-2.5 rounded-full cta px-6 py-3 text-sm font-medium focus-visible:ring-2 focus-visible:ring-cyan-hot/60 focus-visible:outline-none"
        >
          Ir a la consola
          <ArrowRight class="size-4" aria-hidden="true" />
        </a>
      {:else}
        <!-- The pill carries the focus ring (focus-within), because the input
             inside it has none of its own. -->
        <!-- A plain GET form: there is no waitlist backend, and signup IS the
             real flow, so the email simply rides along as ?email= and the
             signup form pre-fills it. SvelteKit turns this into a client-side
             navigation; without JS it still works. -->
        <!-- The hint is described on the INPUT: on a <form> (where it was) no
             screen reader reads it when the field takes focus. -->
        <form
          method="GET"
          action={resolve("signup")}
          class="mt-10 flex w-full max-w-[520px] items-center gap-2 rounded-full border border-hairline-strong bg-panel p-[7px] shadow-(--c3-shadow-float) focus-within:border-accent-edge focus-within:ring-2 focus-within:ring-cyan-hot/50"
        >
          <label for="hero-email" class="sr-only">Tu email</label>
          <input
            id="hero-email"
            name="email"
            type="email"
            autocomplete="email"
            placeholder="operador@ejemplo.com"
            required
            aria-describedby="hero-form-hint"
            class="h-10 min-w-0 flex-1 bg-transparent px-4 font-mono text-base text-ink placeholder:text-ink-mute focus:outline-none sm:px-5 md:text-sm"
          />
          <button
            type="submit"
            class="flex h-10 shrink-0 items-center gap-2 rounded-full cta px-4 text-sm font-medium focus-visible:ring-2 focus-visible:ring-cyan-hot/60 focus-visible:outline-none"
          >
            <span class="hidden sm:inline">Crear cuenta</span>
            <span class="sr-only sm:hidden">Crear cuenta</span>
            <ArrowRight class="size-4" aria-hidden="true" />
          </button>
        </form>
        <p id="hero-form-hint" class="mt-3 text-xs text-ink-mute">
          Te llevamos a crear tu cuenta con este email.
        </p>
      {/if}

      {@render scrollArrow(
        "como-funciona",
        "Ir a ¿Cómo funciona?",
        heroArrowVisible,
        "absolute bottom-10 left-1/2 -translate-x-1/2",
      )}
    </section>

    <!-- How it works -->
    <section
      bind:offsetHeight={howHeight}
      id="como-funciona"
      aria-labelledby="como-funciona-title"
      class="relative scroll-mt-24 px-6 pt-24 pb-16"
    >
      <div class="mx-auto max-w-[1200px]">
        <h2
          id="como-funciona-title"
          class="mb-16 text-center stamp-quiet text-4xl text-ink sm:text-[56px]"
        >
          ¿Cómo funciona?
        </h2>
        <div class="flex flex-col gap-[38px]">
          {#each STEPS as step, i (step.title)}
            {@const imgRight = i % 2 === 0}
            <article
              class="grid overflow-hidden panel md:grid-cols-[1.2fr_1fr]"
            >
              <div
                class={[
                  "flex flex-col justify-center gap-4 px-7 py-10 sm:px-12",
                  !imgRight && "md:order-2",
                ]}
              >
                <h3
                  class="stamp-quiet text-3xl text-ink sm:text-[40px] sm:leading-[1.05]"
                >
                  {step.title}
                </h3>
                <p
                  class="max-w-[480px] text-base leading-relaxed text-ink-mute"
                >
                  {step.body}
                </p>
              </div>
              <!-- The divider follows the layout: a top rule when stacked on
                   a phone, a side rule from md. It used to draw a stray left
                   border inside the stacked card. -->
              <div
                class={[
                  "flex items-center justify-center border-t border-hairline bg-trench p-6 md:border-t-0",
                  imgRight ? "md:border-l" : "md:order-1 md:border-r",
                ]}
              >
                {#if step.screen}
                  <!-- The map screenshot has its own, slightly bluer, dark
                       ground: contained at 260px it was a visible box inside
                       the card with a 4px-tall "BIPED-01" in it. It fills the
                       frame now (cover crops the empty sky and floor, not the
                       trail), fades out at the edges, and its label is
                       repeated in type that can be read. -->
                  <!-- Caption aria-hidden: it repeats the image's alt. -->
                  <div class="flex w-full flex-col items-center gap-3">
                    <img
                      src={step.img}
                      alt={step.alt}
                      width={step.width}
                      height={step.height}
                      loading="lazy"
                      decoding="async"
                      class="aspect-[4/3] w-full [mask-image:radial-gradient(ellipse_closest-side,black_55%,transparent)] object-cover"
                    />
                    <p class="readout" aria-hidden="true">
                      {step.screen.caption}
                    </p>
                  </div>
                {:else}
                  <img
                    src={step.img}
                    alt={step.alt}
                    width={step.width}
                    height={step.height}
                    loading="lazy"
                    decoding="async"
                    class="h-auto max-h-[260px] w-full rounded-lg object-contain"
                  />
                {/if}
              </div>
            </article>
          {/each}
        </div>
      </div>
      {@render scrollArrow(
        "caracteristicas",
        "Ir a Características",
        howArrowVisible,
        "mx-auto mt-14 flex w-fit justify-center",
      )}
    </section>

    <!-- Features -->
    <section
      id="caracteristicas"
      aria-labelledby="caracteristicas-title"
      class="relative scroll-mt-24 px-6 pt-16 pb-32"
    >
      <div class="mx-auto max-w-[1200px]">
        <h2
          id="caracteristicas-title"
          class="mb-14 text-center stamp-quiet text-4xl text-ink sm:text-[56px]"
        >
          Características
        </h2>
        <ul class="grid grid-cols-1 gap-[18px] md:grid-cols-2 lg:grid-cols-3">
          {#each FEATURES as feature, i (feature.title)}
            {@const Icon = feature.icon}
            <!-- One card gets the lift: voice, the way most people will talk
                 to the robot. Every card lifted reads as nothing lifted. -->
            <li
              class={[
                "flex flex-col gap-4 p-7",
                i === 0 ? "panel-hero" : "panel",
              ]}
            >
              <div
                class="flex size-9 items-center justify-center rounded-lg bg-wash text-peri ring-1 ring-accent-edge"
              >
                <Icon class="size-4" aria-hidden="true" />
              </div>
              <h3 class="stamp-quiet text-[22px] text-ink">{feature.title}</h3>
              <p class="text-sm leading-relaxed text-ink-mute">
                {feature.body}
              </p>
            </li>
          {/each}
        </ul>
      </div>
    </section>
  </main>

  <!-- Footer -->
  <footer class="border-t border-hairline px-6 pt-16 pb-10">
    <div class="mx-auto max-w-[1200px]">
      <div class="grid grid-cols-2 gap-12 md:grid-cols-[2fr_1fr_1fr]">
        <div class="col-span-2 md:col-span-1">
          <img
            src="/logo.svg"
            alt="C3PO"
            width="27"
            height="32"
            loading="lazy"
            class="mb-4 h-8 w-auto object-contain glow-cyan"
          />
          <p class="max-w-xs text-sm leading-relaxed text-ink-mute">
            Consola de operador para un Unitree G1, en lenguaje natural.
          </p>
        </div>
        <div>
          <h2 class="mb-4 eyebrow">Producto</h2>
          <ul class="space-y-3 text-sm text-ink-mute">
            <li>
              <a href="#como-funciona" class="transition-colors hover:text-ink"
                >¿Cómo funciona?</a
              >
            </li>
            <li>
              <a
                href="#caracteristicas"
                class="transition-colors hover:text-ink">Características</a
              >
            </li>
            <li>
              <a
                href={resolve(data.user ? "dashboard" : "login")}
                class="transition-colors hover:text-ink"
                >{data.user ? "Consola" : "Ingresar"}</a
              >
            </li>
          </ul>
        </div>
        <div>
          <h2 class="mb-4 eyebrow">Proyecto</h2>
          <ul class="space-y-3 text-sm text-ink-mute">
            <li>
              <a href={REPO} class="transition-colors hover:text-ink"
                >Código fuente</a
              >
            </li>
            <li>
              <a
                href="{REPO}/tree/main/docs"
                class="transition-colors hover:text-ink">Documentación</a
              >
            </li>
          </ul>
        </div>
      </div>
      <p class="mt-12 border-t border-hairline pt-6 text-xs text-ink-mute">
        © {year} C3PO
      </p>
    </div>
  </footer>
</div>
