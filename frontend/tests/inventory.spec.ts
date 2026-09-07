import { expect, test, type Page } from "@playwright/test";
async function openInventory(page: Page, journey: string) {
  await page.goto(`/campaign?inventory=${journey}`);
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "The Missing Courier" }) })
    .getByRole("button", { name: "Open campaign" })
    .click();
  await expect(
    page.getByRole("heading", { name: "The courier’s cellar" }),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Inventory", exact: true })
    .click();
}
async function open(page: Page, journey = "inventory") {
  await openInventory(page, journey);
  await expect(
    page.getByRole("button", { name: "View Bandage", exact: true }),
  ).toBeVisible();
}
async function operation(page: Page, item: string, kind: string) {
  await page.getByRole("button", { name: `View ${item}`, exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("Operation", { exact: true })
    .selectOption(kind);
}
test("character sheet shows authoritative status, effects and points", async ({
  page,
}) => {
  await open(page);
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Character", exact: true })
    .click();
  await expect(page.getByRole("meter", { name: "Hit points" })).toBeVisible();
  for (const name of [
    "Conditions",
    "Attributes",
    "Skills",
    "Defenses",
    "Movement",
    "Derived effects",
    "Points & advancement",
  ])
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
  await page.getByText("Advancement ledger", { exact: true }).click();
  await expect(page.getByText("Ledger version ledger-1")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("keyboard item controls inspect and restore focus", async ({ page }) => {
  await open(page);
  const trigger = page.getByRole("button", {
    name: "View Arming sword",
    exact: true,
  });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("A serviceable steel blade.")).toBeVisible();
  await dialog.getByRole("button", { name: "Confirm inspect" }).click();
  await expect(
    dialog.getByText(
      "You inspect the known item description. Nothing changes.",
    ),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});
test("use retry consumes once and updates the current sheet", async ({
  page,
}) => {
  await open(page, "use-retry");
  await operation(page, "Bandage", "use_item");
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirm use" }).click();
  await dialog.getByRole("button", { name: "Retry same request" }).click();
  await expect(
    dialog.getByText("The item use is committed. One bandage remains."),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.locator(".item-card").filter({
      has: page.getByRole("heading", { name: "Bandage", exact: true }),
    }),
  ).toContainText("Quantity 1");
});
for (const [journey, kind, item, message] of [
  ["illegal-equip", "equip", "Arming sword", "cannot be equipped"],
  ["full-container", "store", "Bandage", "container is full"],
  ["invalid-container", "store", "Bandage", "no longer accessible"],
  ["remote-transfer", "transfer", "Bandage", "no longer within reach"],
]) {
  test(`${journey} exposes the server rejection`, async ({ page }) => {
    await open(page, journey);
    await operation(page, item!, kind!);
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: `Confirm ${kind}` }).click();
    await expect(dialog.getByRole("alert")).toContainText(message!);
    await expect(
      dialog.getByText("Rejected — inventory unchanged"),
    ).toBeVisible();
  });
}
test("drop reconciles weight and focus after removing an item", async ({
  page,
}) => {
  await open(page, "encumbrance");
  await operation(page, "Travel coat", "drop");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm drop" })
    .click();
  await expect(
    page.getByRole("button", { name: "View Travel coat", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".inventory-totals")).toContainText(
    "1800 g carried",
  );
  await expect(page.locator(".inventory-totals")).toContainText(
    "none encumbrance",
  );
  await expect(page.locator("#inventory-title")).toBeFocused();
});
test("confiscated items retain known identity but cannot be used", async ({
  page,
}) => {
  await open(page, "capture");
  await page
    .getByLabel("Location", { exact: true })
    .selectOption("confiscated");
  await operation(page, "Bandage", "use_item");
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByText("Confiscated · custodian and location unknown"),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Confirm use" }),
  ).toBeDisabled();
  await expect(dialog.getByRole("status")).toContainText("custody");
});
test("version conflict requires review without resubmitting", async ({
  page,
}) => {
  await open(page, "inventory-conflict");
  await operation(page, "Bandage", "use_item");
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirm use" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Inventory changed");
  await expect(
    dialog.getByRole("button", { name: "Confirm use" }),
  ).toBeDisabled();
  await dialog
    .getByRole("button", { name: "Review changed inventory" })
    .click();
  await expect(page.locator(".inventory-totals")).toContainText("Version i2");
  await expect(
    page.getByRole("button", { name: "Retry same request" }),
  ).toHaveCount(0);
});
test("filters and narrow layouts preserve access to item operations", async ({
  page,
}) => {
  await open(page);
  await page.getByLabel("Find an item").fill("coat");
  await expect(page.locator(".item-card")).toHaveCount(1);
  await page.getByLabel("Location", { exact: true }).selectOption("stored");
  await expect(page.getByRole("status")).toHaveText(
    "No items match these filters.",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("authorized recovery refresh restores custody and use affordance", async ({
  page,
}) => {
  await open(page, "recovery");
  await expect(page.locator(".item-card").first()).toContainText("confiscated");
  await page.getByRole("button", { name: "Refresh inventory" }).click();
  await expect(page.locator(".item-card").first()).toContainText("carried");
  await operation(page, "Bandage", "use_item");
  await expect(
    page.getByRole("dialog").getByRole("button", { name: "Confirm use" }),
  ).toBeEnabled();
});
test("an empty inventory offers no search or location filter", async ({
  page,
}) => {
  await openInventory(page, "empty");
  await expect(page.getByText("No items in this inventory.")).toBeVisible();
  await expect(page.getByLabel("Find an item")).toHaveCount(0);
  await expect(page.getByLabel("Location", { exact: true })).toHaveCount(0);
});
test("inventory fields are no wider than the content they hold", async ({
  page,
}) => {
  await open(page);
  const search = await page.getByLabel("Find an item").boundingBox();
  expect(search!.width).toBeLessThanOrEqual(480);
  await operation(page, "Bandage", "use_item");
  const quantity = await page
    .getByRole("dialog")
    .getByLabel("Quantity", { exact: true })
    .boundingBox();
  expect(quantity!.width).toBeLessThanOrEqual(96);
});
