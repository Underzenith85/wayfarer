import { test, expect, type Locator, type Page } from "@playwright/test";
// In-app navigation: a reload would drop the in-memory token by design.
const routes = ["Play", "Character", "Inventory", "Journal", "Campaign"];
/** The draft is created from the review step the forward controls lead to. */
async function create(lobby: Locator) {
  await lobby.getByRole("button", { name: "Next: Rules" }).click();
  await lobby.getByRole("button", { name: "Next: Ready" }).click();
  await lobby.getByRole("button", { name: "Create game draft" }).click();
}
async function login(page: Page) {
  await page.goto("/");
  // Isolate this journey's rate-limit budget from other startup scenarios.
  await page
    .getByLabel("Access token", { exact: true })
    .fill("shell-alice-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(/Signed in as/)).toContainText("shell-alice");
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
  await create(lobby);
  await expect(lobby.getByRole("status")).toContainText("revision 0");
  await lobby
    .getByRole("button", { name: "Start a new game", exact: true })
    .click();
  await expect(lobby.getByRole("status")).toHaveCount(0);
  await lobby.getByRole("button", { name: "Adventure", exact: true }).click();
  // A second draft replaces the step view instead of appending another form.
  expect(await page.locator("form").count()).toBe(forms);
  await lobby
    .getByLabel("Adventure and starting party")
    .selectOption("beacon-1");
  await create(lobby);
  await lobby
    .getByLabel("Assign character to shell-alice")
    .selectOption("mira");
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
    await expect(page.getByRole("tablist", { name: "Game menu" })).toHaveCount(
      0,
    );
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
  await expect(page.getByText(/Signed in as/)).toContainText("shell-alice");
  await expect(
    page.getByRole("heading", { name: "Stormbound Harbor" }),
  ).toHaveCount(0);
  await expect(lobby.getByRole("status")).toContainText("In play");
  await lobby.getByRole("button", { name: "Open playing scene" }).click();
  await expect(
    page.getByRole("heading", { name: "Stormbound Harbor" }),
  ).toBeVisible();
});
test("the setup stepper stays one readable line at 320px (#206)", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const lobby = await login(page);
  const steps = lobby.getByRole("navigation", { name: "Setup steps" });
  await expect(steps.getByText("Step 1 of 5: Concept")).toBeVisible();
  const chips = steps.getByRole("listitem");
  await expect(chips).toHaveCount(5);
  // One row, no staircase: every chip shares a top edge, and none of it forces
  // the page to scroll sideways.
  const tops = await chips.evaluateAll((items) =>
    items.map((item) => Math.round(item.getBoundingClientRect().top)),
  );
  expect(new Set(tops).size).toBe(1);
  // The label still names the step it stands for.
  await expect(
    steps.getByRole("button", { name: "Adventure", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("the lobby header is sized to its content and aligned with the panel (#254)", async ({
  page,
}) => {
  await page.goto("/");
  const header = page.getByRole("banner");
  const panel = page.getByRole("region", { name: "New game and lobby" });
  const tabs = page.getByRole("tablist", { name: "Game menu" });
  await expect(tabs).toBeVisible();
  const [card, below, rail] = await Promise.all([
    header.boundingBox(),
    panel.boundingBox(),
    tabs.boundingBox(),
  ]);
  // Two stacked cards read as one column only if they share both edges.
  expect(Math.round(card!.x)).toBe(Math.round(below!.x));
  expect(Math.round(card!.x + card!.width)).toBe(
    Math.round(below!.x + below!.width),
  );
  // The tab row closes the header: no empty card below it, and none of the
  // scene card's 350px floor, which held a wordmark and three tabs.
  expect(card!.y + card!.height - (rail!.y + rail!.height)).toBeLessThanOrEqual(
    8,
  );
  expect(card!.height).toBeLessThan(300);
});
