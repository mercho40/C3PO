<script lang="ts">
  let { percentage = 85, size = 160, stroke = 10 } = $props();

  const radius = $derived((size - stroke) / 2);
  const circumference = $derived(2 * Math.PI * radius);
  const offset = $derived(circumference - (percentage / 100) * circumference);
</script>

<div class="relative" style="width:{size}px;height:{size}px">
  <svg class="size-full -rotate-90" viewBox="0 0 {size} {size}">
    <defs>
      <linearGradient id="cp-grad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#4a7dd1" />
        <stop offset="100%" stop-color="#7ee5ff" />
      </linearGradient>
    </defs>
    <circle
      cx={size / 2}
      cy={size / 2}
      r={radius}
      fill="none"
      stroke="rgba(180,210,255,0.08)"
      stroke-width={stroke}
    />
    <circle
      cx={size / 2}
      cy={size / 2}
      r={radius}
      fill="none"
      stroke="url(#cp-grad)"
      stroke-width={stroke}
      stroke-dasharray={circumference}
      stroke-dashoffset={offset}
      stroke-linecap="round"
      style="transition: stroke-dashoffset 0.6s ease; filter: drop-shadow(0 0 8px rgba(126,229,255,0.45))"
    />
  </svg>
  <div class="absolute inset-0 flex items-end justify-center pb-[28%]">
    <span class="font-display text-[38px] leading-none font-bold tracking-tight text-[#eaf1ff]"
      >{percentage}</span
    >
    <span class="font-heading pb-1 text-lg font-bold text-[#8a96ad]">%</span>
  </div>
</div>
