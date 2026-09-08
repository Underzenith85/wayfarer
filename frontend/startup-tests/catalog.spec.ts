import { test, expect } from "@playwright/test";

test("author a reusable scenario, then start a pinned game from it", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill("author-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  const lobby = page.getByRole("region", { name: "New game and lobby" });
  // Scenario authoring is its own surface, beside the game modes (#261).
  await page.getByRole("tab", { name: "Create scenario", exact: true }).click();
  const catalog = page.getByRole("region", { name: "Scenario catalog" });
  await catalog.getByLabel("Bundled scenario templates").selectOption("0");
  const editor = catalog.getByLabel("Scenario document JSON");
  const document = JSON.parse(await editor.inputValue());
  document.public.title = `Saved beacon ${crypto.randomUUID()}`;
  document.gm_notes = "Private author notes";
  await editor.fill(JSON.stringify(document));
  await catalog.getByRole("button", { name: "Save scenario draft" }).click();
  await expect(catalog.getByRole("status")).toContainText("Saved revision 1");
  await page.reload();
  // The tab stays signed in, so the saved draft is one step away.
  await expect(page.getByLabel("Access token", { exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "Create scenario", exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await catalog
    .getByRole("button", { name: new RegExp(document.public.title) })
    .click();
  await expect(editor).toContainText("Private author notes");
  await catalog.getByRole("button", { name: "Publish saved revision" }).click();
  await expect(
    catalog.getByRole("button", { name: "Create game from revision" }),
  ).toHaveCount(0);
  // Game creation is a separate task. Returning to Start game refreshes the
  // published catalog and exposes the scenario as the one primary choice.
  await page.getByRole("tab", { name: "Start game", exact: true }).click();
  await expect(catalog).toHaveCount(0);
  await lobby
    .getByLabel("Adventure and starting party")
    .selectOption({ label: document.public.title });
  await lobby.getByRole("button", { name: "Next: Rules" }).click();
  await lobby.getByRole("button", { name: "Next: Ready" }).click();
  await lobby.getByRole("button", { name: "Create game draft" }).click();
  await page.getByLabel("Assign character to author").selectOption("mira");
  await lobby.getByRole("button", { name: "Ready", exact: true }).click();
  await page.getByRole("button", { name: "Validate and mark ready" }).click();
  await page.getByRole("button", { name: "Start game", exact: true }).click();
  await expect(
    page.getByText("Stormbound Harbor", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("Private author notes", { exact: true }),
  ).toHaveCount(0);
});
