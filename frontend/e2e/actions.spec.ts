import { expect, test, type Page } from "@playwright/test";

// These tests change data in the database they run against: each run assigns, annotates and closes fresh "new"
// cases (taken from the end of the list). The AI flow at the bottom calls Claude and runs only with E2E_LIVE=1.

const PASSWORD = process.env.E2E_PASSWORD ?? "";
test.skip(!PASSWORD, "set E2E_PASSWORD to the demo users' password");
test.describe.configure({ mode: "serial" });

async function signIn(page: Page, email: string) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
}

/** The last case on the last page of "new" cases: untouched by earlier tests and runs. */
async function freshCase(page: Page, skip = 0): Promise<string> {
  await page.goto("/cases?status=new");
  const last = page.getByRole("link", { name: /^Next$/ });
  while (await last.count()) {
    await last.click();
    await page.waitForLoadState("networkidle");
  }
  const links = page.getByRole("link", { name: /^CASE-\d+$/ });
  const link = links.nth((await links.count()) - 1 - skip);
  const href = (await link.getAttribute("href"))!;
  return href;
}

function actions(page: Page) {
  return page.locator("[data-slot=card]", { has: page.getByRole("heading", { name: "Actions", level: 2, exact: true }) });
}

let workedCase = "";

test("manager assigns a new case to an analyst", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  workedCase = await freshCase(page);
  await page.goto(workedCase);
  const panel = actions(page);
  await expect(panel.getByRole("button", { name: "Run AI investigation" })).toBeVisible();
  await panel.getByLabel("Assign to").selectOption({ label: "Demo Analyst (analyst)" });
  await panel.getByRole("button", { name: "Assign" }).click();
  await expect(page.getByText("Case assigned.")).toBeVisible();
  await expect(page.getByText("Demo Analyst").first()).toBeVisible();
});

test("assigned analyst adds a note and moves the case through investigation", async ({ page }) => {
  await signIn(page, "analyst@demo.finrecon.dev");
  await page.goto(workedCase);
  const panel = actions(page);
  await expect(panel.getByLabel("Assign to")).toHaveCount(0); // analysts cannot assign

  await panel.getByLabel("Add a note").fill("Called PayGate support; ticket 4471 opened.");
  await panel.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByText("Note added.")).toBeVisible();
  await expect(page.getByText("Called PayGate support; ticket 4471 opened.")).toBeVisible();

  await panel.getByLabel("Move case to").selectOption("investigating");
  await panel.getByRole("button", { name: "Update status" }).click();
  await expect(page.getByText("Case status updated.")).toBeVisible();
  await expect(page.locator("h1 + [data-slot=badge]")).toHaveText("Investigating");

  await panel.getByLabel("Move case to").selectOption("awaiting_evidence");
  await panel.getByLabel("Comment (required)").fill("Waiting for the PayGate fee breakdown.");
  await panel.getByRole("button", { name: "Update status" }).click();
  await expect(page.locator("h1 + [data-slot=badge]")).toHaveText("Awaiting evidence");
});

test("an analyst cannot act on a case assigned to nobody", async ({ page }) => {
  await signIn(page, "analyst@demo.finrecon.dev");
  await page.goto(await freshCase(page, 1));
  await expect(actions(page).getByText("There is nothing you can change on this case right now.")).toBeVisible();
});

test("manager dismisses a case with a reason", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await page.goto(await freshCase(page, 1));
  const panel = actions(page);
  await panel.getByLabel("Move case to").selectOption("closed");
  await panel.getByLabel("Reason for closing").selectOption("not_actionable");
  await panel.getByLabel("Comment (required)").fill("Expected bank charge; no correction needed.");
  await panel.getByRole("button", { name: "Update status" }).click();
  await expect(page.locator("h1 + [data-slot=badge]")).toHaveText("Closed");
  await expect(actions(page).getByText("There is nothing you can change on this case right now.")).toBeVisible();
});

test("only managers reach the approvals queue", async ({ page }) => {
  await signIn(page, "manager@demo.finrecon.dev");
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Approvals", level: 1 })).toBeVisible();
  await signIn(page, "analyst@demo.finrecon.dev");
  await page.goto("/approvals");
  await expect(page.getByText("Only managers decide recommendations.")).toBeVisible();
});

test("AI investigation, separation of duties and approval (calls Claude)", async ({ page }) => {
  test.skip(process.env.E2E_LIVE !== "1", "calls Claude (~$0.40); set E2E_LIVE=1");
  test.setTimeout(6 * 60 * 1000);
  await signIn(page, "manager@demo.finrecon.dev");
  const caseUrl = await freshCase(page, 2);
  await page.goto(caseUrl);
  await actions(page).getByRole("button", { name: "Run AI investigation" }).click();
  await expect(page.getByRole("status")).toContainText("Agents working");
  await expect(page.getByText("You started this investigation")).toBeVisible({ timeout: 5 * 60 * 1000 });

  await signIn(page, "approver@demo.finrecon.dev");
  await page.goto("/approvals");
  await page.getByRole("link", { name: /^CASE-/ }).first().click();
  const panel = actions(page);
  await panel.getByLabel("Approve").check();
  await panel.getByRole("button", { name: "Record decision" }).click();
  await expect(page.getByText(/Approved\. The resolution is being carried out\./)).toBeVisible();
});
