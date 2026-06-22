import type { getCookieCache } from "better-auth/cookies";

type CookieSession = NonNullable<Awaited<ReturnType<typeof getCookieCache>>>;

declare global {
	namespace App {
		interface Locals {
			user: CookieSession["user"] | null;
		}
	}
}

export {};
