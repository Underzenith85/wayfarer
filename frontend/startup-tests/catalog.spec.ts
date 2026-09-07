import { test, expect } from "@playwright/test";

test("author a reusable scenario, reopen it, publish and start a pinned game", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill("author-token");
  await page
    .getByRole("button", { name: "Load games and invitations" })
    .click();
  const lobby = page.getByRole("region", { name: "New game and lobby" });
  await lobby.getByRole("button", { name: "Adventure", exact: true }).click();
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
  await page.getByLabel("Access token", { exact: true }).fill("author-token");
  await page
    .getByRole("button", { name: "Load games and invitations" })
    .click();
  await lobby.getByRole("button", { name: "Adventure", exact: true }).click();
  await catalog
    .getByRole("button", { name: new RegExp(document.public.title) })
    .click();
  await expect(editor).toContainText("Private author notes");
  await catalog.getByRole("button", { name: "Publish saved revision" }).click();
  await expect(
    catalog.getByRole("button", { name: "Create game from revision" }),
  ).toBeEnabled();
  // Commit the game draft but lose the acknowledgement. Retry must reuse the exact command.
  await page.route("**/authoring/v1/scenarios/*/instantiate", async (route) => {
    await route.fetch();
    await route.abort();
  });
  await catalog
    .getByRole("button", { name: "Create game from revision" })
    .click();
  await expect(catalog.getByRole("alert")).toBeVisible();
  await page.unroute("**/authoring/v1/scenarios/*/instantiate");
  await catalog
    .getByRole("button", { name: "Retry original scenario request" })
    .click();
  await expect(catalog).toHaveCount(0);
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
