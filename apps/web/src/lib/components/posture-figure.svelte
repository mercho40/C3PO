<script lang="ts">
  /**
   * The robot's body, drawn from its reported FSM mode.
   *
   * Drawn in profile on purpose: from the front, sitting, squatting and lying
   * down are the same silhouette, and those are exactly the states an operator
   * needs to tell apart at a glance.
   *
   * Colour follows `load`, not the mode name — limp, damped, holding, moving —
   * because that is the distinction that decides whether it is safe to walk up
   * to the machine. Motion is the only state that glows, and the stride is the
   * only animation on the page; both are reserved for "this thing is moving
   * right now" so that signal never has to compete with decoration.
   */
  import type { Load, Pose } from "$lib/robot/posture";

  let {
    pose = "unknown",
    load = "unknown",
    class: className = "",
  }: { pose?: Pose; load?: Load; class?: string } = $props();

  type Joints = {
    neck: [number, number];
    hip: [number, number];
    /** near-side arm: elbow, hand */
    arm: [[number, number], [number, number]];
    farArm: [[number, number], [number, number]];
    /** near-side leg: knee, foot */
    leg: [[number, number], [number, number]];
    farLeg: [[number, number], [number, number]];
  };

  // Profile view facing right, ground plane at y=138.
  const POSES: Record<Pose, Joints> = {
    stand: {
      neck: [56, 34],
      hip: [52, 82],
      // Feet apart rather than together: with the legs stacked the profile
      // collapsed into one thick column and stopped reading as a body.
      arm: [
        [59, 60],
        [61, 80],
      ],
      farArm: [
        [48, 60],
        [45, 80],
      ],
      leg: [
        [56, 110],
        [62, 137],
      ],
      farLeg: [
        [47, 110],
        [42, 137],
      ],
    },
    walk: {
      neck: [56, 34],
      hip: [52, 82],
      arm: [
        [46, 60],
        [40, 77],
      ],
      farArm: [
        [62, 60],
        [68, 74],
      ],
      leg: [
        [67, 106],
        [79, 134],
      ],
      farLeg: [
        [43, 112],
        [31, 137],
      ],
    },
    squat: {
      neck: [54, 58],
      hip: [44, 103],
      arm: [
        [58, 82],
        [67, 99],
      ],
      farArm: [
        [51, 83],
        [60, 100],
      ],
      leg: [
        [77, 111],
        [58, 137],
      ],
      farLeg: [
        [71, 113],
        [52, 137],
      ],
    },
    sit: {
      neck: [42, 62],
      hip: [36, 111],
      arm: [
        [45, 88],
        [53, 105],
      ],
      farArm: [
        [38, 89],
        [47, 106],
      ],
      leg: [
        [75, 111],
        [79, 137],
      ],
      farLeg: [
        [70, 114],
        [74, 137],
      ],
    },
    lie: {
      neck: [38, 121],
      hip: [80, 125],
      arm: [
        [58, 134],
        [73, 136],
      ],
      farArm: [
        [58, 110],
        [73, 106],
      ],
      leg: [
        [102, 123],
        [118, 130],
      ],
      farLeg: [
        [102, 130],
        [118, 136],
      ],
    },
    slump: {
      neck: [52, 76],
      hip: [48, 105],
      arm: [
        [55, 97],
        [59, 115],
      ],
      farArm: [
        [48, 98],
        [50, 116],
      ],
      leg: [
        [61, 120],
        [56, 137],
      ],
      farLeg: [
        [54, 121],
        [50, 137],
      ],
    },
    unknown: {
      neck: [56, 34],
      hip: [52, 82],
      arm: [
        [59, 60],
        [61, 80],
      ],
      farArm: [
        [48, 60],
        [45, 80],
      ],
      leg: [
        [56, 110],
        [62, 137],
      ],
      farLeg: [
        [47, 110],
        [42, 137],
      ],
    },
  };

  const j = $derived(POSES[pose] ?? POSES.unknown);

  // The torso and head are rigid plates carried along the spine, so both are
  // drawn once and rotated to match the neck→hip angle. That is what keeps this
  // reading as a machine rather than a stick figure.
  const spineDeg = $derived(
    (Math.atan2(j.hip[1] - j.neck[1], j.hip[0] - j.neck[0]) * 180) / Math.PI -
      90,
  );
  const spineLen = $derived(
    Math.hypot(j.hip[0] - j.neck[0], j.hip[1] - j.neck[1]),
  );

  const moving = $derived(load === "moving");
  const line = (...pts: [number, number][]) =>
    pts.map((p) => p.join(",")).join(" ");
</script>

<!-- Anchored to the bottom of its box (`xMidYMax`) so the ground line lands on
     the floor of whatever panel holds it. Centred, the robot floated in the
     middle of the card and stopped reading as a body standing on something. -->
<svg
  viewBox="0 0 130 150"
  preserveAspectRatio="xMidYMax meet"
  class="posture {className}"
  data-load={load}
  data-moving={moving}
  role="img"
  aria-label="Silueta del robot en postura: {pose}"
>
  <!-- Ground plane. Without it, lying down and standing read the same. -->
  <line class="ground" x1="4" y1="139" x2="126" y2="139" />

  <!-- Far-side limbs sit behind the torso and are drawn dimmer, which is the
       only depth cue a flat profile gets. -->
  <g class="far">
    <polyline points={line(j.neck, j.farArm[0], j.farArm[1])} />
    <polyline points={line(j.hip, j.farLeg[0], j.farLeg[1])} />
  </g>

  <g class="near">
    <!-- Torso plate -->
    <rect
      x={j.neck[0] - 9}
      y={j.neck[1]}
      width="18"
      height={spineLen}
      rx="7"
      class="plate"
      transform="rotate({spineDeg} {j.neck[0]} {j.neck[1]})"
    />
    <!-- Head: a visor, the G1's most recognisable feature -->
    <g transform="rotate({spineDeg} {j.neck[0]} {j.neck[1]})">
      <rect
        x={j.neck[0] - 10}
        y={j.neck[1] - 21}
        width="20"
        height="17"
        rx="6"
        class="plate"
      />
      <line
        class="visor"
        x1={j.neck[0] + 1}
        y1={j.neck[1] - 15}
        x2={j.neck[0] + 7}
        y2={j.neck[1] - 15}
      />
    </g>

    <polyline class="limb" points={line(j.neck, j.arm[0], j.arm[1])} />
    <polyline class="limb" points={line(j.hip, j.leg[0], j.leg[1])} />

    <!-- Joints, so the articulation is legible at small sizes -->
    <circle cx={j.arm[0][0]} cy={j.arm[0][1]} r="2.4" class="joint" />
    <circle cx={j.leg[0][0]} cy={j.leg[0][1]} r="2.6" class="joint" />
    <circle cx={j.hip[0]} cy={j.hip[1]} r="2.6" class="joint" />
  </g>
</svg>

<style>
  .posture {
    width: 100%;
    height: 100%;
    overflow: visible;
    /* Colour is a single custom property so every stroke below stays in sync,
       and only this one line changes between load states. */
    --figure: var(--c3-ink-mute);
  }
  .posture[data-load="limp"] {
    --figure: var(--c3-danger-soft);
  }
  .posture[data-load="damped"] {
    --figure: var(--c3-warn);
  }
  .posture[data-load="holding"] {
    --figure: var(--c3-ink-dim);
  }
  .posture[data-load="moving"] {
    --figure: var(--c3-cyan);
  }

  .ground {
    stroke: var(--c3-hairline-strong);
    stroke-width: 1;
  }

  .limb,
  .far polyline {
    fill: none;
    stroke: var(--figure);
    stroke-width: 4;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .far polyline {
    opacity: 0.34;
  }

  .plate {
    fill: color-mix(in srgb, var(--figure) 14%, transparent);
    stroke: var(--figure);
    stroke-width: 3;
  }

  .visor {
    stroke: var(--figure);
    stroke-width: 3;
    stroke-linecap: round;
  }

  .joint {
    fill: var(--c3-void);
    stroke: var(--figure);
    stroke-width: 2;
  }

  /* The robot has no torque and cannot hold itself up — say so with the stroke,
     the way a schematic marks a member that is not load-bearing. */
  .posture[data-load="limp"] .limb,
  .posture[data-load="limp"] .far polyline {
    stroke-dasharray: 7 4;
  }

  /* No state to report. The body stays whole so it still reads as the robot,
     but it recedes and the limbs go provisional — the pose shown is the last
     shape we knew, not a claim about the pose right now. A dash on the plates
     as well shredded the whole figure into dots. */
  .posture[data-load="unknown"] {
    opacity: 0.55;
  }
  .posture[data-load="unknown"] .limb,
  .posture[data-load="unknown"] .far polyline {
    stroke-dasharray: 6 5;
  }

  .posture[data-moving="true"] .near {
    filter: drop-shadow(
      0 0 9px color-mix(in srgb, var(--c3-cyan) 55%, transparent)
    );
  }

  /* One animation in the whole console, and it only runs while the robot is
     actually walking. */
  @media (prefers-reduced-motion: no-preference) {
    .posture[data-moving="true"] .near .limb:last-of-type,
    .posture[data-moving="true"] .far polyline:last-of-type {
      transform-box: view-box;
      transform-origin: 52px 82px;
      animation: stride 1.1s ease-in-out infinite alternate;
    }
    .posture[data-moving="true"] .far polyline:last-of-type {
      animation-direction: alternate-reverse;
    }
  }

  @keyframes stride {
    from {
      transform: rotate(7deg);
    }
    to {
      transform: rotate(-7deg);
    }
  }
</style>
