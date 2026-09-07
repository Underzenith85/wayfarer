import { test, expect, type Page } from "@playwright/test";
test("two new identities recover invalid generation and reach the opening scene", async ({
  page,
  context,
}) => {
  const room = crypto.randomUUID();
  const guest = await context.newPage();
  await page.goto(`/campaign?onboarding=true&identity=host&room=${room}`);
  await guest.goto(`/campaign?onboarding=true&identity=guest&room=${room}`);
  await page
    .getByRole("button", { name: "Create campaign", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Create invitation", exact: true })
    .click();
  const token = await page
    .getByLabel("Share this invitation code")
    .inputValue();
  await guest.getByLabel("Invitation code", { exact: true }).fill(token);
  await guest
    .getByRole("button", { name: "Join campaign", exact: true })
    .click();
  await expect(
    guest.getByRole("heading", { name: "The Missing Courier", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Refresh lobby" }).click();
  await page.getByRole("button", { name: "Claim Scout", exact: true }).click();
  await guest.getByRole("button", { name: "Refresh lobby" }).click();
  await guest
    .getByRole("button", { name: "Claim Scholar", exact: true })
    .click();
  await page.getByRole("button", { name: "Refresh lobby" }).click();
  await page
    .getByLabel("Character concept", { exact: true })
    .fill("A determined scout");
  await page.getByLabel("Sample generation outcome").selectOption("invalid");
  await page.getByRole("button", { name: "Generate character draft" }).click();
  await expect(
    page.getByText("Build exceeds the 100-point budget."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Finalize Scout" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Load saved revision" }).click();
  await page.getByLabel("Sample generation outcome").selectOption("failure");
  await page.getByRole("button", { name: "Generate character draft" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Your saved draft is unchanged",
  );
  await expect(
    page.getByLabel("Character concept", { exact: true }),
  ).toHaveValue("A determined scout");
  await page.getByLabel("Sample generation outcome").selectOption("success");
  await page.getByRole("button", { name: "Generate character draft" }).click();
  await expect(page.getByText(/Saved revision 2: pending/)).toBeVisible();
  await page.reload();
  await expect(
    page.getByLabel("Character concept", { exact: true }),
  ).toHaveValue("A determined scout");
  await page
    .getByRole("button", { name: "Approve Scout", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Finalize Scout", exact: true })
    .click();
  await page.getByRole("button", { name: "Mark ready", exact: true }).click();
  await guest.getByRole("button", { name: "Refresh lobby" }).click();
  await guest
    .getByLabel("Character concept", { exact: true })
    .fill("A curious scholar");
  await guest.getByRole("button", { name: "Save and validate" }).click();
  await guest.getByRole("button", { name: "Load saved revision" }).click();
  await page.getByRole("button", { name: "Refresh lobby" }).click();
  await page
    .getByRole("button", { name: "Approve Scholar", exact: true })
    .click();
  await guest.getByRole("button", { name: "Refresh lobby" }).click();
  await guest
    .getByRole("button", { name: "Finalize Scholar", exact: true })
    .click();
  await guest.getByRole("button", { name: "Mark ready", exact: true }).click();
  await page.getByRole("button", { name: "Refresh lobby" }).click();
  await page
    .getByRole("button", { name: "Start campaign", exact: true })
    .click();
  for (const p of [page, guest]) {
    if (p === guest)
      await p.getByRole("button", { name: "Refresh lobby" }).click();
    await p
      .getByRole("button", { name: "Enter opening scene", exact: true })
      .click();
    await expect(
      p.getByRole("heading", { name: "The courier’s cellar", exact: true }),
    ).toBeVisible();
    await expect(p.locator("body")).not.toContainText("scrap of blue cloth");
  }
});
test("stale lobby refresh preserves unsaved input", async ({
  page,
  context,
}) => {
  const room = crypto.randomUUID();
  const url = `/campaign?onboarding=true&identity=host&room=${room}`;
  await page.goto(url);
  await page
    .getByRole("button", { name: "Create campaign", exact: true })
    .click();
  await page.getByRole("button", { name: "Claim Scout", exact: true }).click();
  const other: Page = await context.newPage();
  await other.goto(url);
  await page
    .getByLabel("Character concept", { exact: true })
    .fill("Keep my unsaved idea");
  await other
    .getByRole("button", { name: "Create invitation", exact: true })
    .click();
  await page.getByRole("button", { name: "Save and validate" }).click();
  await expect(page.getByRole("alert")).toContainText("lobby changed");
  await page.getByRole("button", { name: "Refresh lobby" }).click();
  await expect(
    page.getByLabel("Character concept", { exact: true }),
  ).toHaveValue("Keep my unsaved idea");
  await page.getByRole("button", { name: "Save and validate" }).click();
  await expect(page.getByText(/Saved revision 1: pending/)).toBeVisible();
});
