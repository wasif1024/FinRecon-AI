import { expect, test, type Page } from "@playwright/test";

const PASSWORD = process.env.E2E_PASSWORD ?? "";
test.skip(!PASSWORD, "set E2E_PASSWORD to the demo users' password");

async function signIn(page: Page, email: string, password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Organisation").fill("demo");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("signed-out visitors are sent to login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});

test("manager signs in, sees the overview and the manager navigation, signs out", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await expect(page.getByRole("heading", { name: /Welcome, Demo Manager/ })).toBeVisible();
  await expect(page.getByText("Awaiting approval")).toBeVisible();

  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByText("Approvals")).toBeVisible();
  await expect(nav.getByText("Audit trail")).toBeVisible();

  const session = (await page.context().cookies()).find((c) => c.name === "finrecon_session");
  expect(session?.httpOnly).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain("finrecon_session");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});

test("analyst navigation hides manager-only areas", async ({ page }) => {
  await signIn(page, "analyst@demo.finrecon.dev");
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByText("Cases")).toBeVisible();
  await expect(nav.getByText("Approvals")).toHaveCount(0);
  await expect(nav.getByText("Audit trail")).toHaveCount(0);
});

test("wrong password shows a generic error", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev", "definitely-not-it");
  await expect(page.getByText("Those details didn't match an account.")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("login refuses to redirect off-site", async ({ page }) => {
  await page.goto("/login?next=//evil.example/steal");
  await page.getByLabel("Email").fill("manager@demo.finrecon.dev");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("http://localhost:3000/");
});
