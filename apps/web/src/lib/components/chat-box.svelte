<script lang="ts">
  import { ArrowUp } from '@lucide/svelte';
  import { Card, CardContent } from '$lib/components/ui/card';

  let messages: Array<{ id: string; text: string; sender: 'user' | 'c3po' }> = [];
  let input = '';

  function sendMessage() {
    if (input.trim()) {
      messages = [
        ...messages,
        {
          id: Date.now().toString(),
          text: input,
          sender: 'user',
        },
      ];
      input = '';
    }
  }
</script>

<Card class="flex flex-col bg-neutral-700 text-white ring-neutral-600 h-full">
  <CardContent class="flex flex-1 flex-col justify-between p-6">
    <!-- Messages Area -->
    <div class="flex-1 space-y-4 overflow-y-auto">
      {#if messages.length === 0}
        <!-- Empty State -->
        <div class="flex h-full flex-col items-center justify-center">
          <div class="mb-8 h-24 w-24 rounded-full border-4 border-neutral-500" />
          <p class="text-4xl font-bold text-white">Start chatting with C3PO</p>
        </div>
      {:else}
        <!-- Messages -->
        {#each messages as message (message.id)}
          <div class="flex {message.sender === 'user' ? 'justify-end' : 'justify-start'}">
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
    <div class="flex gap-4 pt-6">
      <input
        type="text"
        placeholder="Send a message"
        bind:value={input}
        on:keydown={(e) => e.key === 'Enter' && sendMessage()}
        class="flex-1 rounded-full bg-neutral-600 px-8 py-4 text-lg text-white placeholder-neutral-400 focus:outline-none focus:ring-2 focus:ring-neutral-500"
      />
      <button
        on:click={sendMessage}
        class="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-full bg-neutral-600 text-white transition-colors hover:bg-neutral-500"
      >
        <ArrowUp class="h-6 w-6" />
      </button>
    </div>
  </CardContent>
</Card>
