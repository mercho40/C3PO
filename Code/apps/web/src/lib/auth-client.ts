import { createAuthClient } from "better-auth/svelte";
import { organizationClient, adminClient } from "better-auth/client/plugins";
import { PUBLIC_API_URL } from "$env/static/public";

export const authClient = createAuthClient({
	baseURL: PUBLIC_API_URL,
	plugins: [organizationClient(), adminClient()],
});
