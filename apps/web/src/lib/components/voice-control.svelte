<!--
  The voice loop's switch.

  STARTING THIS IS NOT A TOGGLE, IT IS A DECISION. While it runs, anything said
  near the robot is transcribed and handed to an agent that can call every
  bridge tool — so this is the control that turns overheard speech into robot
  motion. The button says what it does rather than "on/off", and the consequence
  is on screen next to it, not in a tooltip.

  "RUNNING" IS NOT "HEARING". With a push-to-talk microphone, silence usually
  means nobody held the button, not that nobody spoke. A loop running over a mic
  that has never opened has done nothing and will do nothing, and saying so is
  more use to an operator than a green light.

  Counters are the loop's own, as it reported them. Nothing here is recomputed —
  same rule as the awareness panel, and for the same reason.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import * as Card from "$lib/components/ui/card/index.js";
  import { Badge } from "$lib/components/ui/badge/index.js";
  import { Button } from "$lib/components/ui/button/index.js";
  import { PUBLIC_API_URL } from "$env/static/public";
  import { VoiceLoopControl } from "$lib/robot/voice-loop.svelte";

  const voice = new VoiceLoopControl(PUBLIC_API_URL);

  onMount(() => {
    voice.start();
    return () => voice.stop();
  });
</script>

<Card.Root>
  <Card.Header>
    <Card.Title class="flex items-center justify-between gap-2">
      <span>Voice loop</span>
      {#if voice.running}
        <Badge variant="default">acting on speech</Badge>
      {:else}
        <Badge variant="secondary">not running</Badge>
      {/if}
    </Card.Title>
  </Card.Header>

  <Card.Content class="space-y-3">
    {#if voice.reason}
      <p class="text-sm text-destructive">{voice.reason}</p>
    {/if}

    {#if voice.running}
      <p class="text-sm text-amber-600 dark:text-amber-500">
        Everything said near the robot is being transcribed and handed to the
        agent, which can call any bridge tool. Stop it before talking about the
        robot rather than to it.
      </p>
    {:else}
      <p class="text-sm text-muted-foreground">
        The robot is listening either way — this decides whether what it hears
        becomes what it does.
      </p>
    {/if}

    {#if voice.state && !voice.canHear}
      <!--
        The distinction that makes silence readable. Not an error: the robot's
        own mic is push-to-talk, so "no audio yet" is the resting state until
        somebody holds the control.
      -->
      <p class="text-sm text-muted-foreground">
        No audio has reached the robot yet, so nothing has been heard — with a
        push-to-talk microphone that usually means nobody held the button, not
        that the room was quiet.
      </p>
    {/if}

    {#if voice.state}
      <dl class="grid grid-cols-3 gap-2 text-sm">
        <div>
          <dt class="text-muted-foreground">Heard</dt>
          <dd class="tabular-nums">{voice.state.utterancesHeard}</dd>
        </div>
        <div>
          <dt class="text-muted-foreground">Agent runs</dt>
          <dd class="tabular-nums">{voice.state.agentRuns}</dd>
        </div>
        <div>
          <dt class="text-muted-foreground">Stops</dt>
          <dd class="tabular-nums">{voice.state.stopsTriggered}</dd>
        </div>
      </dl>

      {#if voice.state.lastHeard}
        <p class="text-sm">
          <span class="text-muted-foreground">Last heard:</span>
          {voice.state.lastHeard}
        </p>
      {/if}

      {#if voice.state.lastError}
        <p class="text-sm text-destructive">{voice.state.lastError}</p>
      {/if}
    {:else if !voice.reason}
      <p class="text-sm text-muted-foreground">Checking…</p>
    {/if}

    <div class="flex gap-2">
      {#if voice.running}
        <Button
          variant="secondary"
          disabled={voice.busy}
          onclick={() => voice.stopLoop()}
        >
          Stop acting on speech
        </Button>
      {:else}
        <Button disabled={voice.busy} onclick={() => voice.startLoop()}>
          Let the robot act on what it hears
        </Button>
      {/if}
    </div>
  </Card.Content>
</Card.Root>
