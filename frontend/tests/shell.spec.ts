import { test, expect } from "@playwright/test";
test("navigation is responsive and transfers focus to the page heading", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByText("No campaign selected", { exact: true }),
  ).toBeVisible();
  for (const name of [
    "Character",
    "Inventory",
    "Journal",
    "Campaign",
    "Play",
  ]) {
    await page
      .getByRole("navigation")
      .getByRole("link", { name, exact: true })
      .click();
    await expect(
      page.getByRole("heading", { level: 1, name, exact: true }),
    ).toBeFocused();
    await expect(
      page.getByRole("navigation").getByRole("link", { name, exact: true }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.reload();
  await expect(
    page.getByRole("heading", { level: 1, name: "Play" }),
  ).toBeVisible();
});
test("campaign-scoped routes open a view directly and survive a reload", async ({
  page,
}) => {
  await page.goto("/c/campaign-1/journal");
  await expect(
    page.getByRole("heading", { level: 1, name: "Journal" }),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Play", exact: true })
    .click();
  await expect(page).toHaveURL(/\/c\/campaign-1$/);
  await expect(
    page.getByRole("heading", { name: "The courier\u2019s cellar" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "The courier\u2019s cellar" }),
  ).toBeVisible();
});
test("sheet traps keyboard focus, closes with Escape and restores trigger", async ({
  page,
}) => {
  // The details drawer only exists below the width that shows the rail.
  await page.setViewportSize({ width: 900, height: 1000 });
  await page.goto("/character");
  const trigger = page.getByRole("button", { name: "Details", exact: true });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Close details" }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("button", { name: "Close details" }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(trigger).toBeFocused();
});
test("\u201cAt a glance\u201d is a rail or a drawer trigger, never both", async ({
  page,
}) => {
  await page.goto("/character");
  const rail = page.getByRole("complementary", { name: "At a glance" });
  const trigger = page.getByRole("button", { name: "Details", exact: true });
  for (const width of [1440, 1101, 1100, 820, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    if (width > 1100) {
      await expect(rail).toBeVisible();
      await expect(trigger).toBeHidden();
    } else {
      await expect(rail).toBeHidden();
      await expect(trigger).toBeVisible();
    }
  }
  await trigger.click();
  await expect(
    page.getByRole("dialog").getByRole("heading", { name: "At a glance" }),
  ).toBeVisible();
});
test("theme persists and offline status recovers", async ({
  page,
  context,
}) => {
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Toggle dark theme" });
  await toggle.click();
  const value = await toggle.getAttribute("aria-pressed");
  await page.reload();
  await expect(toggle).toHaveAttribute("aria-pressed", value!);
  await context.setOffline(true);
  await expect(
    page.getByText("You’re offline. Reconnect to load campaign updates."),
  ).toBeVisible();
  await context.setOffline(false);
  await expect(
    page.getByText("You’re offline. Reconnect to load campaign updates."),
  ).toHaveCount(0);
});
test("skip link reaches main with reduced motion and enlarged text", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: "Play" }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to content" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  await page.evaluate(() => (document.documentElement.style.fontSize = "200%"));
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("the session menu opens on one activation, from pointer and keyboard (#205)", async ({
  page,
}) => {
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Session", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Session" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
});
test("the header shares the column and gutters of the body (#253)", async ({
  page,
}) => {
  await page.goto("/");
  const brand = page.getByRole("link", { name: "WAYFARER" });
  const session = page.getByRole("button", { name: "Session", exact: true });
  const destinations = page.locator("aside.navigation-panel");
  const rail = page.getByRole("complementary", { name: "At a glance" });
  // Above the width where the rail collapses, so both column edges exist.
  for (const width of [2304, 1600, 1280]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(rail).toBeVisible();
    const [logo, menu, left, right] = await Promise.all([
      brand.boundingBox(),
      session.boundingBox(),
      destinations.boundingBox(),
      rail.boundingBox(),
    ]);
    expect(logo!.x).toBeCloseTo(left!.x, 0);
    expect(menu!.x + menu!.width).toBeCloseTo(right!.x + right!.width, 0);
  }
});
