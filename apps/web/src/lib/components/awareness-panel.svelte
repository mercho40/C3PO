<!--
  What the robot sees and hears, for the operator.

  THE ONE THING THIS PANEL MUST NEVER DO is render "no objects" as reassurance.
  D7's whole contract is that an empty scene and an unobserved scene are
  different facts, so when the detector is offline this shows the warning
  instead of an empty list — and the warning is the bridge's own words, not a
  string invented here.

  Ages and staleness come from the robot as reported. Nothing is recomputed
  against the browser's clock: a console that disagrees with the robot about how
  old a reading is gives the operator no way to tell which one is wrong.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import * as Card from "$lib/components/ui/card/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Awareness } from "$lib/robot/awareness.svelte";

  const awareness = new Awareness();

  onMount(() => {
    awareness.start();
    return () => awareness.stop();
  });

  // Bearings are egocentric: 0 ahead, POSITIVE LEFT (D7). Rendering that sign
  // backwards would put every obstacle on the wrong side of the robot, so the
  // words are derived here in one place rather than at each call site.
  function side(bearingDeg: number): string {
    const b = Math.round(bearingDeg);
    if (Math.abs(b) <= 15) return "ahead";
    if (Math.abs(b) >= 165) return "behind";
    return b > 0 ? `${Math.abs(b)}° left` : `${Math.abs(b)}° right`;
  }

  function statusTone(
    s: string | undefined,
  ): "default" | "secondary" | "destructive" {
    if (s === "ok") return "default";
    if (s === "stale") return "secondary";
    return "destructive";
  }
</script>

<div class="grid gap-4 md:grid-cols-2">
  <Card.Root>
    <Card.Header>
      <Card.Title class="flex items-center justify-between gap-2">
        <span>Sees</span>
        <span class="flex gap-1">
          {#each Object.entries(awareness.surroundings?.sources ?? {}) as [name, status] (name)}
            <Badge variant={statusTone(status)} class="text-[10px] uppercase">
              {name}: {status}
            </Badge>
          {/each}
        </span>
      </Card.Title>
    </Card.Header>
    <Card.Content class="space-y-3">
      {#if awareness.unreachable}
        <p class="text-sm text-destructive">{awareness.reason}</p>
      {:else if !awareness.surroundings}
        <p class="text-sm text-muted-foreground">
          Waiting for the first snapshot…
        </p>
      {:else}
        <!--
          Notes BEFORE objects, deliberately. They carry "detection is offline,
          this is not an empty scene" — which an operator must read before, not
          after, concluding the way is clear.
        -->
        {#each awareness.surroundings.notes ?? [] as note (note)}
          <p class="text-sm text-amber-600 dark:text-amber-500">{note}</p>
        {/each}

        {#if awareness.objects.length > 0}
          <ul class="space-y-1 text-sm">
            {#each awareness.objects as obj (obj.label + obj.bearing_deg)}
              <li class="flex items-baseline justify-between gap-3">
                <span class="font-medium">{obj.label}</span>
                <span class="text-muted-foreground tabular-nums">
                  {obj.range_m.toFixed(1)} m · {side(obj.bearing_deg)}
                </span>
              </li>
            {/each}
          </ul>
        {:else if awareness.detectorOnline}
          <!-- Only sayable when the detector is actually running. -->
          <p class="text-sm text-muted-foreground">
            Nothing detected right now.
          </p>
        {/if}

        {#if (awareness.surroundings.objects_omitted ?? 0) > 0}
          <!-- Truncation is always declared (D7) — a list that quietly drops
               things is worse than a shorter one that says so. -->
          <p class="text-xs text-muted-foreground">
            + {awareness.surroundings.objects_omitted} more not listed
          </p>
        {/if}
      {/if}
    </Card.Content>
  </Card.Root>

  <Card.Root>
    <Card.Header>
      <Card.Title class="flex items-center justify-between gap-2">
        <span>Hears</span>
        {#if awareness.voice}
          <Badge
            variant={awareness.voice.always_listening ? "default" : "secondary"}
            class="text-[10px] uppercase"
          >
            {awareness.voice.always_listening ? "always on" : "push-to-talk"}
          </Badge>
        {/if}
      </Card.Title>
    </Card.Header>
    <Card.Content class="space-y-3">
      {#if !awareness.voice}
        <p class="text-sm text-muted-foreground">No voice telemetry.</p>
      {:else if awareness.voice.error}
        <p class="text-sm text-destructive">{awareness.voice.error}</p>
      {:else}
        {#if !awareness.voice.always_listening && !awareness.voice.mic_ever_open}
          <!--
            The most important sentence in this panel. With a push-to-talk mic,
            an empty transcript almost always means nobody held the button —
            NOT that the room was silent. An operator who reads it the other way
            concludes the robot ignored someone.
          -->
          <p class="text-sm text-amber-600 dark:text-amber-500">
            The robot has not been able to hear anything. Its microphone only
            opens while someone holds <strong>L1+L2</strong> on the remote — this
            is not silence in the room.
          </p>
        {/if}

        {#if awareness.voice.recent.length > 0}
          <ul class="space-y-1 text-sm">
            {#each awareness.voice.recent
              .slice(-8)
              .reverse() as item, i (item.text + item.age_s + i)}
              <li class="flex items-baseline justify-between gap-3">
                <span
                  class={item.kind === "stop"
                    ? "font-semibold text-destructive"
                    : ""}
                >
                  {item.kind === "stop" ? `⏹ ${item.text}` : item.text}
                </span>
                <span class="text-xs text-muted-foreground tabular-nums">
                  {item.age_s.toFixed(0)}s ago
                </span>
              </li>
            {/each}
          </ul>
        {:else if awareness.voice.mic_ever_open}
          <p class="text-sm text-muted-foreground">Nothing said recently.</p>
        {/if}
      {/if}
    </Card.Content>
  </Card.Root>
</div>
