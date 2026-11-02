import { expect, test, type Page } from "@playwright/test";

// Adds a user to the database it runs against (a unique email per run), as the other suites change cases.
const PASSWORD = process.env.E2E_PASSWORD ?? "";
test.skip(!PASSWORD, "set E2E_PASSWORD to the demo users' password");

async function signIn(page: Page, email: string, password = PASSWORD) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("admin adds a user, who signs in; deactivated, they cannot", async ({ page }) => {
  test.setTimeout(60_000); // four sign-ins against a production build
  const email = `e2e.${Date.now()}@demo.finrecon.dev`;
  const password = "e2e user password 1";
  await signIn(page, "admin@demo.finrecon.dev");
  await page.getByRole("navigation", { name: "Main" }).getByText("Users").click();
  await expect(page.getByRole("heading", { name: "Users", exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: "admin@demo.finrecon.dev" }).getByRole("button", { name: "Deactivate" }))
    .toHaveCount(0); // not yourself

  // The form streams in after the page heading; the page also has other labels that mention emails.
  await expect(page.getByRole("heading", { name: "Add a user" })).toBeVisible();
  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await page.getByLabel("Full name").fill("E2E Analyst");
  await page.getByLabel("Initial password").fill(password);
  await page.getByRole("button", { name: "Add user" }).click();
  await expect(page.getByText("E2E Analyst can now sign in.")).toBeVisible();
  const row = page.getByRole("row", { name: email });
  await expect(row.getByRole("checkbox", { name: `analyst for ${email}` })).toBeChecked();

  await signIn(page, email, password);
  await expect(page.getByRole("heading", { name: /Welcome, E2E Analyst/ })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" }).getByText("Users")).toHaveCount(0);

  await signIn(page, "admin@demo.finrecon.dev");
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible(); // the sign-in has finished
  await page.goto("/users");
  await page.getByRole("row", { name: email }).getByRole("button", { name: "Deactivate" }).click();
  await expect(page.getByText(/User deactivated/)).toBeVisible();
  await expect(page.getByRole("row", { name: email }).getByText("Deactivated")).toBeVisible();

  await signIn(page, email, password);
  await expect(page.getByText("Those details didn't match an account.")).toBeVisible();
});

test("only admins manage users", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" }).getByText("Users")).toHaveCount(0);
  await page.goto("/users");
  await expect(page.getByText("Only admins manage users.")).toBeVisible();
});
