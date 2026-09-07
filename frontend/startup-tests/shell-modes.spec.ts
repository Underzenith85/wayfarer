import { test, expect, type Page } from "@playwright/test";
// In-app navigation: a reload would drop the in-memory token by design.
const routes = ["Play", "Character", "Inventory", "Journal", "Campaign"];
async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill("alice-token");
  await page
    .getByRole("button", { name: "Load games and invitations" })
    .click();
  await expect(page.getByText(/Signed in as/)).toContainText("alice");
  return page.getByRole("region", { name: "New game and lobby" });
}
test("play replaces setup, and a second draft replaces the step view", async ({
  page,
}) => {
  const lobby = await login(page);
  // One creation surface at a time: the catalog and the brief never stack.
  await lobby.getByRole("button", { name: "Concept", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Scenario catalog" }),
  ).toHaveCount(0);
  await lobby.getByRole("button", { name: "Adventure", exact: true }).click();
  await expect(page.getByLabel("Premise", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Scenario catalog" }),
  ).toBeVisible();
  const forms = await page.locator("form").count();
  await lobby
    .getByLabel("Adventure and starting party")
    .selectOption("beacon-1");
  await lobby.getByRole("button", { name: "Create game draft" }).click();
  await expect(lobby.getByRole("status")).toContainText("revision 0");
  await lobby.getByRole("button", { name: "New draft", exact: true }).click();
  await expect(lobby.getByRole("status")).toHaveCount(0);
  await lobby.getByRole("button", { name: "Adventure", exact: true }).click();
  // A second draft replaces the step view instead of appending another form.
  expect(await page.locator("form").count()).toBe(forms);
  await lobby
    .getByLabel("Adventure and starting party")
    .selectOption("beacon-1");
  await lobby.getByRole("button", { name: "Create game draft" }).click();
  await lobby.getByLabel("Assign character to alice").selectOption("mira");
  await lobby.getByRole("button", { name: "Ready", exact: true }).click();
  await lobby.getByRole("button", { name: "Validate and mark ready" }).click();
  await lobby.getByRole("button", { name: "Start game", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Stormbound Harbor" }),
  ).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  for (const route of routes) {
    const link = navigation.getByRole("link", { name: route, exact: true });
    await link.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#page-title")).toHaveText(route);
    await expect(
      page.getByRole("region", { name: "New game and lobby" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("navigation", { name: "Game menu" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Session", exact: true }),
    ).toBeVisible();
    // Nothing is rendered above the game shell on any route.
    const top = await page
      .locator("header.topbar")
      .evaluate((node) => node.getBoundingClientRect().top + window.scrollY);
    expect(top).toBe(0);
  }
  // Setup stays reachable from the play header, with the session and the
  // campaign that was being played both retained.
  await page.getByRole("button", { name: "Session", exact: true }).click();
  await page
    .getByRole("dialog", { name: "Session" })
    .getByRole("button", { name: "Switch campaign" })
    .click();
  await expect(page.getByText(/Signed in as/)).toContainText("alice");
  await expect(
    page.getByRole("heading", { name: "Stormbound Harbor" }),
  ).toHaveCount(0);
  await expect(lobby.getByRole("status")).toContainText("active");
  await lobby.getByRole("button", { name: "Open playing scene" }).click();
  await expect(
    page.getByRole("heading", { name: "Stormbound Harbor" }),
  ).toBeVisible();
});
