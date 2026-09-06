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
test("sheet traps keyboard focus, closes with Escape and restores trigger", async ({
  page,
}) => {
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
