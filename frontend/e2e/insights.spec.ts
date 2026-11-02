import { readFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

const PASSWORD = process.env.E2E_PASSWORD ?? "";
test.skip(!PASSWORD, "set E2E_PASSWORD to the demo users' password");

async function signIn(page: Page, email: string) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
}

test("overview shows the latest run with an accessible chart", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await expect(page.getByRole("heading", { name: "Latest reconciliation run" })).toBeVisible();
  await expect(page.getByRole("meter", { name: "Gateway to settlement" })).toBeVisible();
  await expect(page.getByText("Never added across currencies.")).toBeVisible();
  await page.getByText("Show as table").click();
  await expect(page.getByRole("cell", { name: "Fee discrepancy" })).toBeVisible();
});

test("a malformed upload fails cleanly and re-running reconciliation reuses the run", async ({ page }) => {
  await signIn(page, "analyst@demo.finrecon.dev");
  await page.getByRole("navigation", { name: "Main" }).getByText("Data & runs").click();
  await page.getByLabel("File for Demo Bank Statement").setInputFiles({
    name: "broken.csv", mimeType: "text/csv", buffer: Buffer.from(`only,wrong,columns\n${Date.now()},x,y\n`),
  });
  await page.getByRole("button", { name: "Upload" }).first().click();
  await expect(page.getByText(/broken\.csv: 0 accepted, 0 rejected \(failed\)/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "broken.csv" }).first()).toBeVisible();

  await page.getByRole("button", { name: "Reconcile selected batches" }).click();
  await expect(page.getByText(/Run finished: \d+ exceptions, 0 new cases opened\./)).toBeVisible();
});

test("policy search returns cited passages; analysts cannot upload", async ({ page }) => {
  await signIn(page, "analyst@demo.finrecon.dev");
  await page.goto("/policies");
  await expect(page.getByRole("heading", { name: "Upload a policy" })).toHaveCount(0);
  await page.getByLabel("Search policies").fill("bank credit arrived a week late");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/q=bank/);
  await expect(page.getByText(/Settlement Operations Policy v1/).first()).toBeVisible();
  await expect(page.getByText(/Late settlements/).first()).toBeVisible();
});

test("audit trail filters and verifies the hash chain", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await page.goto("/audit");
  await page.getByRole("button", { name: "Verify chain" }).click();
  await expect(page.getByText(/Intact: all [\d,]+ events match their recorded hashes\./)).toBeVisible();
  await page.getByLabel("Event type").fill("approval.decided");
  await page.getByRole("button", { name: "Filter" }).click();
  await expect(page.getByRole("cell", { name: "approval.decided" }).first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "case.created" })).toHaveCount(0);
});

test("reports render and download as CSV and PDF", async ({ page }) => {
  await signIn(page, "analyst@demo.finrecon.dev");
  await page.goto("/reports");
  await expect(page.getByRole("heading", { name: "Reconciliation summary", level: 2 })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Gateway to settlement" })).toBeVisible();

  await page.getByRole("link", { name: /Outstanding exceptions/ }).click();
  await expect(page.getByRole("columnheader", { name: "Age (days)" })).toBeVisible();
  await expect(page.getByText(/^Total (USD|EUR|GBP)$/).first()).toBeVisible();

  const csv = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download CSV" }).click();
  const csvFile = await csv;
  expect(csvFile.suggestedFilename()).toMatch(/^finrecon-outstanding-exceptions-.*\.csv$/);
  expect(readFileSync((await csvFile.path())!, "utf8")).toContain("Outstanding exceptions");

  const pdf = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download PDF" }).click();
  const pdfFile = await pdf;
  expect(readFileSync((await pdfFile.path())!).subarray(0, 4).toString()).toBe("%PDF");
});

test("report downloads require a session", async ({ request }) => {
  const response = await request.get("/api/reports/duplicates?format=csv", { maxRedirects: 0 });
  expect(response.status()).not.toBe(200);
});
