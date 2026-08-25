<!--
  The voice loop's switch.

  This starts a spoken conversation. The agent answers ordinary dialogue and
  only uses robot tools when the speaker clearly requests a physical task.
  Nearby speech can still reach the microphone, so the session remains an
  explicit operator decision rather than an ambient default.

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
      <span>Voice conversation</span>
      {#if voice.running}
        <Badge variant="default">conversation active</Badge>
      {:else}
        <Badge variant="secondary">conversation off</Badge>
      {/if}
    </Card.Title>
  </Card.Header>

  <Card.Content class="space-y-3">
    {#if voice.reason}
      <p class="text-sm text-destructive">{voice.reason}</p>
    {/if}

    {#if voice.running}
      <p class="text-sm text-amber-600 dark:text-amber-500">
        The robot will answer naturally and remember this conversation. It uses
        robot tools only when you clearly request a physical task. End the session
        before talking about the robot rather than to it.
      </p>
    {:else}
      <p class="text-sm text-muted-foreground">
        Start a voice session to talk with the robot. Ordinary conversation gets
        a spoken reply; clear task requests may use robot tools.
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
      {#if voice.state.engine === "openai-realtime"}
        <p class="text-xs text-muted-foreground">
          OpenAI Realtime · speech-to-speech · transcript saved automatically
        </p>
      {/if}
      <dl class="grid grid-cols-3 gap-2 text-sm">
        <div>
          <dt class="text-muted-foreground">Heard</dt>
          <dd class="tabular-nums">{voice.state.utterancesHeard}</dd>
        </div>
        <div>
          <dt class="text-muted-foreground">Turns</dt>
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

      {#if voice.state.conversation?.phase !== "idle"}
        <p class="text-sm text-muted-foreground">
          {voice.state.conversation?.phase === "streaming" ? "Thinking…" : "Speaking…"}
        </p>
      {:else if voice.state.conversation?.lastTurn}
        <p class="text-xs text-muted-foreground">
          First speech:
          {voice.state.conversation.lastTurn.firstSpeechMs === null
            ? "—"
            : `${Math.round(voice.state.conversation.lastTurn.firstSpeechMs)} ms`}
          · total {Math.round(voice.state.conversation.lastTurn.totalMs)} ms
        </p>
      {/if}

      {#if voice.state.lastReply}
        <p class="text-sm">
          <span class="text-muted-foreground">Last reply:</span>
          {voice.state.lastReply}
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
          End voice conversation
        </Button>
      {:else}
        <Button disabled={voice.busy} onclick={() => voice.startLoop()}>
          Start voice conversation
        </Button>
      {/if}
      {#if voice.state?.chatId}
        <Button variant="outline" href={`/chat?id=${voice.state.chatId}`}>
          Open transcript
        </Button>
      {/if}
    </div>
  </Card.Content>
</Card.Root>
