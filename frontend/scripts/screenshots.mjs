// Captures the README screenshots into docs/screenshots/ from the demo (scripts/demo.sh, synthetic data only).
//   E2E_PASSWORD=<demo password> node scripts/screenshots.mjs   (dashboard on http://localhost:3000)
import { chromium } from "@playwright/test";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const OUT = new URL("../../docs/screenshots/", import.meta.url).pathname;
const password = process.env.E2E_PASSWORD;
if (!password) throw new Error("set E2E_PASSWORD");

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

async function signIn(email) {
  await page.context().clearCookies();
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: /Welcome/ }).waitFor();
}

async function shot(name, { clipTo } = {}) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage: !clipTo, clip: clipTo });
  console.log(`docs/screenshots/${name}.png`);
}

await signIn("manager@demo.finrecon.dev");
await page.getByRole("heading", { name: "Latest reconciliation run" }).waitFor();
await shot("overview", { clipTo: { x: 0, y: 0, width: 1440, height: 870 } });

await page.goto(`${BASE}/cases?status=awaiting_evidence`);
await page.getByRole("link", { name: "CASE-000013" }).click();
await page.getByRole("heading", { name: "Approval history" }).waitFor();
await shot("case-detail", { clipTo: { x: 0, y: 0, width: 1440, height: 1400 } });

await page.goto(`${BASE}/cases?status=new`);
await page.getByRole("link", { name: /^CASE-/ }).first().click();
await page.getByRole("heading", { name: "Actions", exact: true }).waitFor();
await shot("case-actions", { clipTo: { x: 0, y: 0, width: 1440, height: 900 } });

await signIn("approver@demo.finrecon.dev");
await page.goto(`${BASE}/approvals`);
await page.getByRole("heading", { name: "Approvals", exact: true }).waitFor();
await shot("approvals", { clipTo: { x: 0, y: 0, width: 1440, height: 900 } });

await page.goto(`${BASE}/reports?kind=outstanding-exceptions`);
await page.getByRole("columnheader", { name: "Age (days)" }).waitFor();
await shot("reports", { clipTo: { x: 0, y: 0, width: 1440, height: 900 } });

await page.goto(`${BASE}/audit`);
await page.getByRole("button", { name: "Verify chain" }).click();
await page.getByText(/Intact: all/).waitFor();
await shot("audit", { clipTo: { x: 0, y: 0, width: 1440, height: 900 } });

await signIn("admin@demo.finrecon.dev");
await page.goto(`${BASE}/users`);
await page.getByRole("heading", { name: "Add a user" }).waitFor();
await shot("users", { clipTo: { x: 0, y: 0, width: 1440, height: 760 } });

await browser.close();
