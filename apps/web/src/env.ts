import { building } from "$app/env";
import { defineEnvVars } from "@sveltejs/kit/env";

// What each variable means, and its gotchas, lives in .env.example.
const optional = (value: string | undefined) => value ?? "";

export const variables = defineEnvVars({
  // Inlined at build time, so it must be set for `vite build`. It is public
  // anyway, so baking it into the bundle exposes nothing.
  PUBLIC_API_URL: { public: true, static: true },

  // Read when the app starts, so the secret never lands in the build output,
  // where Turborepo would cache it and a cache hit could ship a stale secret
  // after rotation. Optional while building, required at runtime.
  BETTER_AUTH_SECRET: {
    schema: (value) => {
      if (!building && !value)
        throw new Error("BETTER_AUTH_SECRET must be set.");
      return value ?? "";
    },
  },

  // Optional, read when the app starts. Empty means "not configured": the
  // pages fall back to the host serving the console, or skip the robot camera.
  PUBLIC_ROBOT_CAM_URL: { public: true, schema: optional },
  PUBLIC_SIM_CAM_HOST: { public: true, schema: optional },
  PUBLIC_TELEOP_HOST: { public: true, schema: optional },
  PUBLIC_TELEOP_PORT: { public: true, schema: optional },
});
