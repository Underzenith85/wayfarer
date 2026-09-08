import { expect, test, type Page } from "@playwright/test";
async function open(page: Page, journey = "resolve") {
  await page.goto(`/campaign?journey=${journey}`);
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "The Missing Courier" }) })
    .getByRole("button", { name: "Open campaign" })
    .click();
  await expect(
    page.getByRole("heading", { name: "The courier’s cellar" }),
  ).toBeVisible();
}
/**
 * "At a glance" is a persistent rail at wide widths and a drawer below that
 * breakpoint, so a test reads the summary from whichever one this viewport has.
 */
async function glance(page: Page) {
  const trigger = page.getByRole("button", { name: "Details", exact: true });
  if (!(await trigger.isVisible()))
    return page.getByRole("complementary", { name: "At a glance" });
  await trigger.click();
  return page.getByRole("dialog");
}
async function send(page: Page, text = "Use a bandage.") {
  await page.getByLabel("What do you do?").fill(text);
  await page.getByRole("button", { name: "Send action", exact: true }).click();
}
test("send resolves with authoritative trace and versioned summaries", async ({
  page,
}) => {
  await open(page);
  await send(page);
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  // The narrative answer is the turn's body, and it is what a player reads
  // first — before the engine's account of the same turn (#294).
  const turn = page.locator(".transcript-entry").last();
  await expect(turn.locator(".gm-message")).toContainText(
    "You draw the clean linen tight",
  );
  await page.getByText("Rolls and consequences", { exact: true }).click();
  const rolls = turn.locator(".committed-result > details");
  await expect(
    page.getByText("First Aid: 2 + 3 + 4 against 12", { exact: false }),
  ).toBeVisible();
  // What changed is named; the versions naming it to the service are not (#296).
  await expect(rolls).toContainText("Updated your character");
  await expect(rolls.getByText("h2", { exact: false })).toBeHidden();
  await rolls.getByText("Technical details", { exact: true }).click();
  await expect(rolls.getByText("h2", { exact: false }).first()).toBeVisible();
  const summary = await glance(page);
  await expect(summary.getByText("Character version h2")).toBeHidden();
  await summary.getByText("Technical details", { exact: true }).click();
  await expect(summary.getByText("Character version h2")).toBeVisible();
  await expect(summary.getByText("Inventory version i2")).toBeVisible();
  await page.keyboard.press("Escape");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("clarification continues the original action", async ({ page }) => {
  await open(page, "clarify");
  await send(page, "Look closer.");
  await expect(
    page.getByRole("region", { name: "Pending choice" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "The locked door", exact: true })
    .click();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  await expect(page.locator(".transcript-entry")).toHaveCount(1);
});
test("rejected action reports at the composer and stays out of the story", async ({
  page,
}) => {
  await open(page, "reject");
  await send(page);
  const failure = page.getByRole("region", { name: "Failed attempts" });
  await expect(
    failure.getByText("That item is not accessible from this scene."),
  ).toBeVisible();
  // Nothing reached the game, so nothing is written into the log (#298).
  await expect(page.locator(".transcript-entry")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Authoritative result" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Retry same request" }),
  ).toHaveCount(0);
  await failure.getByRole("button", { name: "Dismiss" }).click();
  await expect(failure).toHaveCount(0);
});
test("unknown acknowledgement retries without duplicating an action", async ({
  page,
}) => {
  await open(page, "retry");
  await send(page);
  await page.getByRole("button", { name: "Retry same request" }).click();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  await expect(page.locator(".transcript-entry")).toHaveCount(1);
  await expect(page.getByLabel("What do you do?")).toHaveValue("");
});
test("narration failure leaves committed results visible", async ({ page }) => {
  await open(page, "narration-failure");
  await send(page);
  await expect(
    page.getByText("Narration unavailable. Your committed result is saved."),
  ).toBeVisible();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
});
test("drafts survive campaign switching and reload without cross-campaign leakage", async ({
  page,
}) => {
  await open(page);
  await page.getByLabel("What do you do?").fill("A private draft");
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Campaign", exact: true })
    .click();
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "Lights on the Sound" }) })
    .getByRole("button")
    .click();
  await expect(page.getByLabel("What do you do?")).toHaveValue("");
  // The campaign is in the URL, so a reload returns to the same table.
  await expect(page).toHaveURL(/\/c\/campaign-2$/);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "The silent quay" }),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Campaign", exact: true })
    .click();
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "The Missing Courier" }) })
    .getByRole("button")
    .click();
  await expect(page.getByLabel("What do you do?")).toHaveValue(
    "A private draft",
  );
});
test("session expiry removes private campaign state", async ({ page }) => {
  await open(page, "expired");
  await send(page);
  await expect(
    page.getByRole("heading", { name: "Session ended" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "The courier’s cellar" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).filter((k) => k.startsWith("wayfarer:draft")),
    ),
  ).toEqual([]);
});
