<!--
  "Consola" — what this console is pointed at, read-only, and its shortcuts.

  Nothing here is editable from the browser on purpose: these are deployment
  facts (apps/web/.env, apps/bridge's SIM_MODE), and a console that let you
  retarget the robot from a settings page would be one misclick away from
  driving the physical G1 while believing it drives the simulator.

  The bridge's environment is the robot's own answer (`get_state.env`), not a
  config guess — and when the link is down it says it is the last answer.
-->
<script lang="ts">
  import {
    PUBLIC_API_URL,
    PUBLIC_ROBOT_CAM_URL,
    PUBLIC_SIM_CAM_HOST,
  } from "$app/env/public";
  import { getRobotLive, getVoiceLoop } from "#lib/robot/context.js";
  import { LINK } from "#lib/robot/link-copy.js";
  import {
    ENGINE_LABEL,
    defaultEngineOf,
  } from "#lib/components/voice/voice-view.js";

  const live = getRobotLive();
  const voice = getVoiceLoop();

  const robotCam = PUBLIC_ROBOT_CAM_URL.trim();
  const simHost = PUBLIC_SIM_CAM_HOST.trim();

  const ENV_LABEL: Record<string, string> = {
    stub: "Stub — datos ficticios, sin robot",
    isaac: "Isaac Sim — simulador",
    real: "Robot físico (G1)",
  };

  const bridgeEnv = $derived.by(() => {
    const e = live.state?.env;
    const label = e ? (ENV_LABEL[e] ?? e) : null;
    if (live.online) return label ?? "—";
    return label
      ? `${LINK[live.problem ?? "offline"]} — último informado: ${label}`
      : LINK[live.problem ?? "offline"];
  });
</script>

<section
  class="flex min-w-0 flex-col gap-4 panel p-5"
  aria-labelledby="console-heading"
>
  <h2 id="console-heading" class="eyebrow">Consola</h2>

  <dl class="flex flex-col gap-3">
    <div class="flex min-w-0 flex-col gap-1">
      <dt class="eyebrow">Puente</dt>
      <dd class="text-sm break-words text-ink">{bridgeEnv}</dd>
    </div>
    <div class="flex min-w-0 flex-col gap-1">
      <dt class="eyebrow">Servidor (API)</dt>
      <dd class="font-mono text-xs break-all text-ink-dim">{PUBLIC_API_URL}</dd>
    </div>
    <div class="flex min-w-0 flex-col gap-1">
      <dt class="eyebrow">Cámara</dt>
      <dd class="text-sm break-words text-ink-dim">
        {#if robotCam}
          Robot · <span class="font-mono text-xs break-all">{robotCam}</span>
        {:else}
          Simulador (WebRTC) · <span class="font-mono text-xs"
            >{simHost || "el host de esta consola"}</span
          >
        {/if}
      </dd>
    </div>
    <div class="flex min-w-0 flex-col gap-1">
      <dt class="eyebrow">Voz</dt>
      <dd class="text-sm text-ink-dim">
        {voice.state
          ? `${ENGINE_LABEL[defaultEngineOf(voice.state)]} · predeterminado`
          : "—"}
      </dd>
    </div>
  </dl>

  <p class="text-xs text-ink-mute">
    Se configuran en el despliegue (apps/web/.env y el puente), no desde acá.
  </p>

  <div class="flex flex-col gap-2 border-t border-hairline pt-4">
    <h3 class="eyebrow">Atajos de teclado</h3>
    <dl class="flex flex-col gap-2 text-sm">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <dt class="text-ink-dim">Parar todo (PARAR)</dt>
        <dd>
          <kbd
            class="rounded border border-hairline-strong px-1.5 py-0.5 font-mono text-xs text-ink"
            >Shift</kbd
          >
          +
          <kbd
            class="rounded border border-hairline-strong px-1.5 py-0.5 font-mono text-xs text-ink"
            >Espacio</kbd
          >
        </dd>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <dt class="text-ink-dim">Mostrar u ocultar el menú lateral</dt>
        <dd>
          <kbd
            class="rounded border border-hairline-strong px-1.5 py-0.5 font-mono text-xs text-ink"
            >Ctrl</kbd
          >
          /
          <kbd
            class="rounded border border-hairline-strong px-1.5 py-0.5 font-mono text-xs text-ink"
            >⌘</kbd
          >
          +
          <kbd
            class="rounded border border-hairline-strong px-1.5 py-0.5 font-mono text-xs text-ink"
            >B</kbd
          >
        </dd>
      </div>
    </dl>
    <!-- Both shortcuts share one "typing" test (`isTypingTarget` in
         estop.ts): text inputs, text areas, selects and contenteditable. Keep
         this sentence in step with it — the e-stop one especially, since an
         operator who believes Shift+Espacio always works will reach for it
         from inside a field. -->
    <p class="text-xs leading-relaxed text-ink-mute">
      Ninguno de los dos funciona mientras el foco está en un campo donde se
      escribe (texto, área de texto o lista desplegable): así un espacio con
      Shift no frena al robot por accidente. Desde un campo, usá el botón PARAR
      de arriba.
    </p>
  </div>
</section>
