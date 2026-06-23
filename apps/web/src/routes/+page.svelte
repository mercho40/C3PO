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

{#snippet arrowIcon(cls = "size-3.5")}
  <svg class={cls} viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M2 7H12M8 11L12 7L8 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
{/snippet}

{#snippet voiceIcon()}
  <svg class="size-3.5" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M9 3.5C9 2.39543 8.10457 1.5 7 1.5C5.89543 1.5 5 2.39543 5 3.5V6.5C5 7.60457 5.89543 8.5 7 8.5C8.10457 8.5 9 7.60457 9 6.5V3.5Z" stroke="currentColor" stroke-width="1.3" />
    <path d="M3 7C3 8.06087 3.42143 9.07828 4.17157 9.82843C4.92172 10.5786 5.93913 11 7 11M7 11C8.06087 11 9.07828 10.5786 9.82843 9.82843C10.5786 9.07828 11 8.06087 11 7M7 11V12.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
  </svg>
{/snippet}

{#snippet chartIcon()}
  <svg class="size-3.5" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M1 7H4L5.5 3L7.5 11L9 7H13" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
{/snippet}

{#snippet scrollArrow(href: string, label: string, visible: boolean, extra = "")}
  <a
    {href}
    aria-label={label}
    class="text-[#8a96ad]/50 transition-all duration-500 hover:text-[#8a96ad] {extra} {visible
      ? 'opacity-100'
      : 'pointer-events-none opacity-0'}"
  >
    <svg class="size-4 animate-bounce" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
    </svg>
  </a>
{/snippet}

{#snippet howCard(title: string, description: string, img: string, imgAlt: string, imgRight: boolean)}
  <div
    class="grid overflow-hidden rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] md:grid-cols-[1.2fr_1fr]"
  >
    <div class="flex flex-col justify-center gap-4 px-7 py-10 sm:px-12 {imgRight ? '' : 'md:order-2'}">
      <h3 class="font-heading text-3xl font-bold tracking-tight text-[#eaf1ff] sm:text-[40px] sm:leading-[1.05]">
        {title}
      </h3>
      <p class="max-w-[480px] text-base leading-relaxed text-[#8a96ad]">{description}</p>
    </div>
    <div
      class="flex items-center justify-center bg-[#06121c] p-6 {imgRight
        ? 'border-l border-[rgba(180,210,255,0.08)]'
        : 'border-[rgba(180,210,255,0.08)] md:order-1 md:border-r'}"
    >
      <img src={img} alt={imgAlt} class="max-h-[260px] w-full rounded-[10px] object-contain" />
    </div>
  </div>
{/snippet}

{#snippet featureCard(title: string, description: string, icon: "voice" | "chart", cls: string)}
  <div
    class="flex flex-col gap-4 overflow-hidden rounded-[14px] border border-[rgba(180,210,255,0.08)] bg-gradient-to-b from-[#0c1220] to-[#121828] p-7 {cls}"
  >
    <div class="flex size-9 items-center justify-center rounded-lg bg-[rgba(159,197,255,0.14)] text-[#9fc5ff]">
      {#if icon === "voice"}{@render voiceIcon()}{:else}{@render chartIcon()}{/if}
    </div>
    <h3 class="font-heading text-[22px] font-bold tracking-tight text-[#eaf1ff]">{title}</h3>
    <p class="text-[13px] leading-relaxed text-[#8a96ad]">{description}</p>
  </div>
{/snippet}

<div class="font-display relative min-h-screen overflow-x-hidden bg-[#05070d] text-[#eaf1ff]">
  <!-- Radial glows -->
  <div
    class="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[1200px]"
    style="background:
      radial-gradient(120% 60% at 50% 0%, rgba(159,197,255,0.14), rgba(159,197,255,0) 60%),
      radial-gradient(50% 40% at 90% 35%, rgba(74,125,209,0.10), rgba(74,125,209,0) 70%);"
  ></div>

  <!-- Navbar -->
  <nav class="fixed inset-x-0 top-4 z-50 mx-auto grid max-w-[1461px] grid-cols-[1fr_auto_1fr] items-center px-6">
    <img src="/logo.svg" alt="C3PO" class="h-9 w-auto justify-self-start object-contain drop-shadow-[0_0_12px_rgba(126,229,255,0.45)]" />
    <div
      class="hidden items-center gap-10 rounded-full border border-[rgba(180,210,255,0.2)] bg-[#0c1220]/85 px-12 py-3.5 text-sm font-medium text-[#c6dcff] shadow-[0_8px_30px_-12px_rgba(126,229,255,0.45)] backdrop-blur-md md:flex"
    >
      <a href="#como-funciona" class="transition-colors hover:text-white">¿Cómo funciona?</a>
      <a href="#features" class="transition-colors hover:text-white">Características</a>
      <a href="/chat" class="transition-colors hover:text-white">Contactanos</a>
    </div>
    <div class="flex items-center gap-3.5 justify-self-end">
      <a
        href={data.user ? "/dashboard" : "/login"}
        class="rounded-full px-4 py-2 text-sm text-[#c6dcff] transition-colors hover:text-white"
      >
        Ingresar
      </a>
      <a
        href={data.user ? "/dashboard" : "/signup"}
        class="flex items-center gap-2.5 rounded-full bg-gradient-to-b from-[rgba(227,249,255,0.92)] to-[rgba(172,238,255,0.92)] px-5 py-2.5 text-sm font-medium text-[#06121c] shadow-[0px_8px_28px_-10px_rgba(126,229,255,0.55)] transition-transform hover:scale-[1.02]"
      >
        {data.user ? "Dashboard" : "Crear usuario"}
        {@render arrowIcon()}
      </a>
    </div>
  </nav>

  <!-- Hero -->
  <section
    bind:this={heroSection}
    class="relative flex min-h-screen flex-col items-center justify-center px-6 pb-20 text-center"
  >
    <h1 class="font-display text-7xl font-medium tracking-[-0.04em] text-[#d9d9d9] sm:text-[116px] sm:leading-none">
      C3PO
    </h1>
    <p class="mt-6 max-w-[660px] text-lg leading-relaxed text-[#8a96ad]">
      Un espacio para operar robots. Comandos por voz, telemetría en tiempo real,
      navegación autónoma y control de seguridad — todo desde el navegador.
    </p>
    <form
      class="mt-10 flex w-full max-w-[520px] items-center gap-2 rounded-full border border-[rgba(180,210,255,0.18)] bg-[#0c1220] p-[7px] shadow-[0px_0px_65px_-23px_rgba(126,229,255,0.55)]"
      onsubmit={handleWaitlist}
    >
      <input
        type="email"
        bind:value={email}
        placeholder="ejemplo@gmail.com"
        required
        class="font-mono h-10 flex-1 bg-transparent px-5 text-[13px] text-[rgba(234,241,255,0.71)] placeholder:text-[rgba(234,241,255,0.5)] focus:outline-none"
      />
      <button
        type="submit"
        aria-label="Unirme"
        class="flex size-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-[rgba(198,220,255,0.92)] to-[rgba(159,197,255,0.92)] text-[#06121c] shadow-[0px_8px_28px_-10px_rgba(126,229,255,0.55)] transition-transform hover:scale-105"
      >
        {@render arrowIcon()}
      </button>
    </form>
    {@render scrollArrow("#como-funciona", "Ir a ¿Cómo funciona?", heroArrowVisible, "absolute bottom-10 left-1/2 -translate-x-1/2")}
  </section>

  <!-- How it works -->
  <section bind:this={comoFuncionaSection} id="como-funciona" class="relative px-6 pt-24 pb-16">
    <div class="mx-auto max-w-[1200px]">
      <h2 class="mb-16 text-center font-display text-4xl font-medium tracking-tight text-[#eaf1ff] sm:text-[60px]">
        ¿Cómo funciona?
      </h2>
      <div class="flex flex-col gap-[38px]">
        {@render howCard(
          "Conectá tu robot",
          "Vincula tu robot a través de WiFi. C3PO maneja la integración sin que tengas que tocar código.",
          "/landing/robot-standing.png",
          "Robot Unitree G1",
          true,
        )}
        {@render howCard(
          "Seguí todo desde tu pantalla",
          "Monitorea cámaras en vivo, ubicación GPS, batería y estado del robot desde la web en tiempo real.",
          "/landing/chart.png",
          "Telemetría en vivo",
          false,
        )}
        {@render howCard(
          "Indicá con voz o chat",
          "Habla en tu idioma o escribe órdenes naturales. C3PO entiende contexto, no solo comandos técnicos.",
          "/landing/robot-hand.png",
          "Control por voz",
          true,
        )}
      </div>
    </div>
    {@render scrollArrow("#features", "Ir a Características", comoFuncionaArrowVisible, "mt-14 flex justify-center")}
  </section>

  <!-- Features -->
  <section id="features" class="relative px-6 pt-16 pb-32">
    <div class="mx-auto max-w-[1200px]">
      <h2 class="mb-14 text-center font-display text-4xl font-medium tracking-tight text-[#eaf1ff] sm:text-[60px]">
        Features
      </h2>
      <div class="grid auto-rows-[260px] grid-cols-1 gap-[18px] md:grid-cols-6">
        {@render featureCard(
          "Comandos por voz",
          "Hablale al robot sin botones ni pantallas. Entiende contexto, no solo palabras clave.",
          "voice",
          "md:col-span-3 md:row-span-2 md:h-auto",
        )}
        {@render featureCard(
          "Telemetría en vivo",
          "Batería, torque articular, IMU, red — sub-50ms en cualquier parte.",
          "chart",
          "md:col-span-3",
        )}
        {@render featureCard(
          "Perfil de uso",
          "Historial de sesiones, kilómetros recorridos y estadísticas de operación por unidad.",
          "chart",
          "md:col-span-3",
        )}
        {@render featureCard(
          "Vista de cámaras",
          "Feed directo desde los sensores del robot con detección de objetos en tiempo real.",
          "chart",
          "md:col-span-4",
        )}
        {@render featureCard(
          "Navegación autónoma",
          "El robot detecta obstáculos, lee el entorno y toma decisiones de movimiento solo.",
          "chart",
          "md:col-span-2",
        )}
      </div>
    </div>
  </section>

  <!-- Footer -->
  <footer class="border-t border-[rgba(180,210,255,0.08)] px-6 pt-16 pb-10">
    <div class="mx-auto max-w-[1200px]">
      <div class="grid grid-cols-2 gap-12 md:grid-cols-4">
        <!-- Brand -->
        <div class="col-span-2 md:col-span-1">
          <img src="/logo.svg" alt="C3PO" class="mb-4 h-8 w-auto object-contain drop-shadow-[0_0_12px_rgba(126,229,255,0.45)]" />
          <p class="mb-6 max-w-xs text-sm leading-relaxed text-[#8a96ad]">
            Controlá cualquier robot Unitree con lenguaje natural.
          </p>
          <div class="flex items-center gap-3">
            <a href="https://linkedin.com" aria-label="LinkedIn" class="flex size-8 items-center justify-center rounded-lg border border-[rgba(180,210,255,0.08)] text-[#8a96ad] transition-colors hover:border-[rgba(180,210,255,0.3)] hover:text-[#eaf1ff]">
              <svg class="size-4" fill="currentColor" viewBox="0 0 24 24"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" /></svg>
            </a>
            <a href="https://instagram.com" aria-label="Instagram" class="flex size-8 items-center justify-center rounded-lg border border-[rgba(180,210,255,0.08)] text-[#8a96ad] transition-colors hover:border-[rgba(180,210,255,0.3)] hover:text-[#eaf1ff]">
              <svg class="size-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z" /></svg>
            </a>
            <a href="https://x.com" aria-label="X / Twitter" class="flex size-8 items-center justify-center rounded-lg border border-[rgba(180,210,255,0.08)] text-[#8a96ad] transition-colors hover:border-[rgba(180,210,255,0.3)] hover:text-[#eaf1ff]">
              <svg class="size-4" fill="currentColor" viewBox="0 0 24 24"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.744l7.737-8.835L1.254 2.25H8.08l4.253 5.622 5.91-5.622zm-1.161 17.52h1.833L7.084 4.126H5.117z" /></svg>
            </a>
          </div>
        </div>
        <!-- Producto -->
        <div>
          <h4 class="mb-4 text-xs font-semibold tracking-widest text-[#eaf1ff] uppercase">Producto</h4>
          <ul class="space-y-3 text-sm text-[#8a96ad]">
            <li><a href="#como-funciona" class="transition-colors hover:text-[#eaf1ff]">¿Cómo funciona?</a></li>
            <li><a href="#features" class="transition-colors hover:text-[#eaf1ff]">Características</a></li>
            <li><a href="/chat" class="transition-colors hover:text-[#eaf1ff]">Chat</a></li>
            <li><a href="/dashboard" class="transition-colors hover:text-[#eaf1ff]">Dashboard</a></li>
          </ul>
        </div>
  </footer>
</div>
