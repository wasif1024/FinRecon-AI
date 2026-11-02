import { expect, test, type Page } from "@playwright/test";

const PASSWORD = process.env.E2E_PASSWORD ?? "";
test.skip(!PASSWORD, "set E2E_PASSWORD to the demo users' password");

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
}

test("case list filters and paginates through the URL", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await page.getByRole("navigation", { name: "Main" }).getByText("Cases").click();
  await expect(page.getByRole("heading", { name: "Cases" })).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(26); // header + one page of 25

  await page.getByRole("link", { name: "Next" }).click();
  await expect(page).toHaveURL(/page=2/);

  await page.goto("/cases");
  await page.getByLabel("Status").selectOption("awaiting_evidence");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/status=awaiting_evidence/);
  await expect(page.getByRole("link", { name: "CASE-000013" })).toBeVisible();
});

test("case detail shows the whole investigation", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await page.goto("/cases?status=awaiting_evidence");
  await page.getByRole("link", { name: "CASE-000013" }).click();

  await expect(page.getByRole("heading", { name: "CASE-000013" })).toBeVisible();
  await expect(page.getByText("2.53 GBP").first()).toBeVisible();

  // Recommendation, executed after a different manager approved it.
  await expect(page.getByText("Request documentation").first()).toBeVisible();
  await expect(page.getByText(/Executed .* case moved to Awaiting evidence/)).toBeVisible();

  // Findings grouped by kind, each with evidence links that resolve on the page.
  await expect(page.getByRole("heading", { name: /Verified facts/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Outstanding requirements/ })).toBeVisible();
  const firstEvidenceLink = page.getByRole("link", { name: /^(\d·)?[A-Z]\d+$/ }).first();
  const target = await firstEvidenceLink.getAttribute("href");
  await expect(page.locator(target!)).toBeVisible();

  // Policy citations name the document and section.
  await expect(page.getByText(/^Payment Processing Fee Policy v1 › 2\. Fee discrepancy thresholds$/).first()).toBeVisible();

  // Matching engine's rule-by-rule result.
  await expect(page.getByRole("cell", { name: "Fee", exact: true })).toBeVisible();
  await expect(page.getByText("pg_000053").first()).toBeVisible();

  // Approval history names the approver; the manager also sees the audit trail.
  await expect(page.getByText("by Demo Approver")).toBeVisible();
  await expect(page.getByText("resolution.executed")).toBeVisible();
});

test("analysts do not see the audit trail", async ({ page }) => {
  await signIn(page, "analyst@demo.finrecon.dev");
  await page.goto("/cases?status=awaiting_evidence");
  await page.getByRole("link", { name: "CASE-000013" }).click();
  await expect(page.getByRole("heading", { name: "CASE-000013" })).toBeVisible();
  await expect(page.getByText("Approval history")).toBeVisible();
  await expect(page.getByText("Audit trail", { exact: true })).toHaveCount(0);
});

test("unknown and malformed case IDs show not found", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  for (const id of ["00000000-0000-0000-0000-000000000000", "1%20OR%201=1"]) {
    await page.goto(`/cases/${id}`);
    await expect(page.getByRole("heading", { name: "Case not found" })).toBeVisible();
  }
});

test("untrusted record text is shown as text, never run", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await page.goto("/cases?status=awaiting_evidence");
  await page.getByRole("link", { name: "CASE-000013" }).click();
  let dialog = false;
  page.on("dialog", () => { dialog = true; });
  const record = page.locator("details", { hasText: "Original record for" }).first();
  await record.locator("summary").click();
  await expect(record.locator("pre")).toBeVisible();
  expect(dialog).toBe(false);
  expect(await page.locator("script:not([src])").evaluateAll((s) => s.some((x) => x.textContent?.includes("alert(")))).toBe(false);
});
