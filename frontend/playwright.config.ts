import { defineConfig } from "@playwright/test";

// End-to-end tests against a running backend (uv run finrecon serve) and the built frontend.
// Credentials come from the environment: E2E_PASSWORD for the demo users.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: "chrome", // the locally installed Chrome; no browser download
    trace: "retain-on-failure",
  },
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: "pnpm start",
    url: "http://localhost:3000/login",
    reuseExistingServer: true,
  },
});
