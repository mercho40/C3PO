import adapter from "@sveltejs/adapter-vercel";
import tailwindcss from "@tailwindcss/vite";
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    tailwindcss(),
    // SvelteKit 3 takes its configuration here rather than in svelte.config.js.
    // Options that aren't SvelteKit's own are forwarded to vite-plugin-svelte.
    sveltekit({
      // Deployed to Vercel as Serverless Functions. Pinned to the Node runtime
      // (not edge) because hooks.server.ts runs Better Auth's getCookieCache,
      // which needs Node crypto. See https://svelte.dev/docs/kit/adapter-vercel
      adapter: adapter({ runtime: "nodejs22.x" }),
      compilerOptions: {
        // Force runes mode for the project, except for libraries.
        runes: ({ filename }) =>
          filename.split(/[/\\]/).includes("node_modules") ? undefined : true,
      },
    }),
  ],
});
