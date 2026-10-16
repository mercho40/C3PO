<!--
  The bridge's faults, as sentences an operator can act on.

  Shared by the dashboard and the map aside, which used to carry two copies of
  the same markup printing raw tokens —
  `fsm_rpc_failing_12_polls__restart_the_bridge_if_this_persists` — with the
  one piece of advice buried in an underscore-joined identifier. Translation
  lives in `#lib/robot/faults.ts`; the raw token stays underneath as a readout,
  because whoever is debugging the bridge needs the exact string.
-->
<script lang="ts">
  import { ShieldCheck, TriangleAlert, CircleAlert } from "@lucide/svelte";
  import { readFaults } from "#lib/robot/faults.js";
  import { LINK, type LinkProblem } from "#lib/robot/link-copy.js";

  let {
    faults,
    online,
    problem = null,
    simulated = false,
    compact = false,
  }: {
    /** Raw tokens from `/state`. */
    faults: readonly string[];
    /** The last poll reached the bridge. Offline faults are not current. */
    online: boolean;
    /**
     * Why it is offline (`RobotLive.problem`). Without it this always said
     * "Sin conexión", under a topbar saying "Sin puente" for the same fault.
     */
    problem?: LinkProblem | null;
    /** Stub mode: an empty list is fabricated, not a clean bill of health. */
    simulated?: boolean;
    compact?: boolean;
  } = $props();

  const items = $derived(readFaults(faults));

  // Keyed on the token's SHAPE (digits stripped), so `stale_pose_34s` →
  // `stale_pose_36s` updates one row in place instead of replacing it — and
  // the live region below, which only announces additions, does not re-read
  // every fault on every 2 s poll.
  const key = (raw: string) => raw.replace(/[\d.]+/g, "#");
</script>

<div aria-live="polite" aria-relevant="additions">
  {#if !online}
    <p class="text-sm text-ink-mute">
      {LINK[problem ?? "offline"]}: no hay una lectura actual de fallos.
    </p>
  {:else if items.length === 0}
    {#if simulated}
      <!-- A fabricated robot has no faults by construction. Green would be a
           clean bill of health for a machine that does not exist. -->
      <p class="flex items-center gap-2 text-sm text-ink-mute">
        <ShieldCheck class="size-4 shrink-0" aria-hidden="true" />
        Sin fallos (datos ficticios)
      </p>
    {:else}
      <p class="flex items-center gap-2 text-sm text-ok">
        <ShieldCheck class="size-4 shrink-0" aria-hidden="true" />
        Sin fallos
      </p>
    {/if}
  {:else}
    <ul class={["flex flex-col", compact ? "gap-2.5" : "gap-3"]}>
      {#each items as fault (key(fault.raw))}
        {@const Icon =
          fault.severity === "danger" ? CircleAlert : TriangleAlert}
        <li class="flex items-start gap-2">
          <Icon
            class={[
              "mt-0.5 size-4 shrink-0",
              fault.severity === "danger" ? "text-danger-soft" : "text-warn",
            ]}
            aria-hidden="true"
          />
          <div class="flex min-w-0 flex-col gap-0.5">
            <span
              class={[
                "text-sm",
                fault.severity === "danger" ? "text-danger-soft" : "text-warn",
              ]}
            >
              {fault.text}
            </span>
            {#if fault.action}
              <span class={["text-ink-dim", compact ? "text-xs" : "text-sm"]}
                >{fault.action}</span
              >
            {/if}
            <span class="readout break-all">
              {fault.scope === "bridge" ? "puente" : "robot"} · {fault.raw}
            </span>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>
