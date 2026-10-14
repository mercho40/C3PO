<!--
  What is wrong with the headset view, in order of what to believe.

  These distinctions are each a past wrong diagnosis:
  - a GL failure in the camera layer is not the robot (`xrCameraBroken`);
  - "never attempted" is not "camera dead" (`realHardware`), and neither is
    "this console has no camera URL" (`camConfigured`);
  - "no frame EVER" is not "frozen" (`xrCameraEverHadFrame` vs `xrCameraLive`) —
    conflating them told an operator perception was down during an ordinary
    one-second stall, with a real picture in front of them.

  Each callout leads with the operator's sentence; the commands to run are for
  whoever fixes it, and are folded under "Detalles".
-->
<script lang="ts">
  import Callout from "#lib/components/callout.svelte";
  import DetailsNote from "./details-note.svelte";
  import type { VrControl } from "#lib/robot/vr-control.svelte.js";

  let { ctl, realHardware }: { ctl: VrControl; realHardware: boolean } =
    $props();
</script>

{#if ctl.xrCameraBroken}
  <Callout tone="warn" title="La capa de cámara falló al dibujar">
    Es un problema de WebGL en el visor — un shader que no compiló —, no del
    robot ni de la cámara. El seguimiento de cabeza sigue funcionando: se
    descartó la imagen y se conservó la pose, a propósito. Salí y volvé a entrar
    a VR para reintentar con un contexto nuevo.
  </Callout>
{:else if !realHardware}
  <Callout tone="warn" title="La cámara ni siquiera se intentó">
    El puente no reportó <code>SIM_MODE=real</code>, así que esta página nunca
    abrió el stream — no es un problema de la cámara ni del túnel.
    <DetailsNote>
      <p>
        Suele ser que falta <code>-L 8001</code> en el túnel, que
        <code>c3po-bridge.service</code> no está activo, o que
        <code>apps/bridge/.env</code> sigue en <code>stub</code>. Corré
        <code>./scripts/preflight.sh</code>.
      </p>
    </DetailsNote>
  </Callout>
{:else if !ctl.camConfigured}
  <!-- Before "no frame ever": with no URL the headset never ASKED for one, and
       blaming the robot's vision process for this console's .env sent the
       operator to debug a robot that was fine. -->
  <Callout tone="warn" title="La consola no tiene configurada la cámara">
    Esta consola no sabe dónde pedir la imagen, así que el visor nunca la pidió.
    No es el robot ni su cámara.
    <DetailsNote>
      <p>
        Falta <code>PUBLIC_ROBOT_CAM_URL</code> en <code>apps/web/.env</code>
        (ver <code>apps/web/.env.example</code>). Después de configurarla,
        reiniciá el servidor de la consola.
      </p>
    </DetailsNote>
  </Callout>
{:else if !ctl.xrCameraEverHadFrame}
  <Callout tone="warn" title="Sin imagen en el visor">
    No llegó ningún cuadro de la cámara. Casi siempre es que el proceso de
    visión no está corriendo en el robot.
    <DetailsNote>
      <p>
        Es <code>perception_up perception</code>: el proceso que tiene la D435i
        y sirve el stream en 8081.
      </p>
    </DetailsNote>
  </Callout>
{:else if !ctl.xrCameraLive}
  <Callout tone="warn" title="La imagen quedó congelada">
    Llegaron cuadros y dejaron de llegar. Lo que ves en el visor es una foto
    vieja, atenuada a propósito para que se note. No manejes con eso.
    <DetailsNote>
      <p>
        Como sí llegaron cuadros, <code>perception_up</code> está corriendo — lo que
        falló son sus ticks, o el enlace.
      </p>
    </DetailsNote>
  </Callout>
{/if}

{#if ctl.xrMode !== "immersive-ar"}
  <Callout tone="warn" title="Sesión sin passthrough">
    El visor no dio <code>immersive-ar</code>, así que puede que no veas nada
    más que negro: el overlay del navegador no se compone en modo VR puro. Los
    controles siguen funcionando a ciegas, pero salí de VR si necesitás verlos.
  </Callout>
{/if}
