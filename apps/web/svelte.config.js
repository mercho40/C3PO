import adapter from "@sveltejs/adapter-vercel";

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		// Deployed to Vercel as Serverless Functions. Pinned to the Node runtime
		// (not edge) because hooks.server.ts runs Better Auth's getCookieCache,
		// which needs Node crypto. See https://svelte.dev/docs/kit/adapter-vercel
		adapter: adapter({ runtime: "nodejs22.x" }),
		alias: {
			"@back/*": "../back/src/*",
		},
	},
	vitePlugin: {
		dynamicCompileOptions: ({ filename }) =>
			filename.includes('node_modules') ? undefined : { runes: true }
	}
};

export default config;
