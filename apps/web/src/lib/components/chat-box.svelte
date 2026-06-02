<script lang="ts">
  import { ArrowUp } from "@lucide/svelte";
  import { Card, CardContent } from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Button } from "$lib/components/ui/button";

  type Message = { id: string; text: string; sender: "user" | "c3po" };

  let messages = $state<Message[]>([]);
  let input = $state("");

  function sendMessage() {
    if (input.trim()) {
      messages = [
        ...messages,
        { id: crypto.randomUUID(), text: input, sender: "user" },
      ];
      input = "";
    }
  }
</script>

<Card class="flex h-full flex-col bg-neutral-700 text-white ring-neutral-600">
  <CardContent class="flex flex-1 flex-col justify-between p-6">
    <!-- Messages Area -->
    <div class="flex-1 space-y-4 overflow-y-auto">
      {#if messages.length === 0}
        <!-- Empty State -->
        <div class="flex h-full flex-col items-center justify-center">
          <div
            class="mb-8 h-24 w-24 rounded-full border-4 border-neutral-500"
          ></div>
          <p class="text-4xl font-bold text-white">Start chatting with C3PO</p>
        </div>
      {:else}
        <!-- Messages -->
        {#each messages as message (message.id)}
          <div
            class="flex {message.sender === 'user'
              ? 'justify-end'
              : 'justify-start'}"
          >
            <div
              class="max-w-xs rounded-lg px-4 py-2 {message.sender === 'user'
                ? 'bg-blue-600 text-white'
                : 'bg-neutral-600 text-neutral-200'}"
            >
              {message.text}
            </div>
          </div>
        {/each}
      {/if}
    </div>

    <!-- Input Area -->
    <div class="flex items-center gap-4 pt-6">
      <Input
        bind:value={input}
        placeholder="Send a message"
        onkeydown={(e) => e.key === "Enter" && sendMessage()}
        class="h-12 flex-1 rounded-full border-transparent bg-neutral-600 px-6 text-base text-white placeholder:text-neutral-400"
      />
      <Button
        size="icon"
        onclick={sendMessage}
        aria-label="Send message"
        class="h-12 w-12 rounded-full bg-neutral-600 text-white hover:bg-neutral-500"
      >
        <ArrowUp class="h-5 w-5" />
      </Button>
    </div>
  </CardContent>
</Card>
