import { test, expect } from "@playwright/test";

test("workshop validates edits, previews profiles and activates saved builds", async ({
  page,
  request,
}) => {
  const created = await request.post("http://127.0.0.1:8000/test-workshop", {
    headers: { Authorization: "Bearer gm-token" },
  });
  expect(created.ok()).toBeTruthy();
  const { campaign_id } = (await created.json()) as { campaign_id: string };
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill("alice-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator(`[data-resume-id="${campaign_id}"]`).click();
  await page.getByRole("link", { name: "Character", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Character workshop" }),
  ).toBeVisible();
  await expect(page.locator(".point-budget")).toContainText("points remaining");
  const strength = page.locator(".purchase-row").filter({
    has: page.locator('select option[value="attribute:st"]:checked'),
  });
  await strength.getByRole("button", { name: "Increase Strength" }).click();
  await expect(strength.locator(".purchase-cost")).toContainText("10 pts");
  await expect(
    page.getByRole("region", { name: "Derived statistics" }),
  ).toContainText("11");
  await page.getByLabel("Name", { exact: true }).fill("Reviewed hero");
  await page.getByRole("button", { name: "Save and validate" }).click();
  await expect(
    page.getByRole("button", { name: "Activate approved draft" }),
  ).toBeEnabled();
  await page.getByLabel("Name", { exact: true }).fill("Unsaved edit");
  await expect(
    page.getByRole("button", { name: "Activate approved draft" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Save and validate" }).click();
  await expect(
    page.getByRole("button", { name: "Activate approved draft" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Activate approved draft" }).click();
  await expect(
    page.getByRole("button", { name: "Activate approved draft" }),
  ).toBeDisabled();
  await page
    .getByLabel("Rules profile", { exact: true })
    .selectOption("profile:gurps-basic-set-4e-2004@3");
  await expect(
    page.getByText(
      "Preview only. Changing the campaign profile requires an explicit migration.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save and validate" }),
  ).toBeDisabled();
  const view = await request.get(
    `http://127.0.0.1:8000/campaigns/${campaign_id}/workshop/a`,
    {
      headers: { Authorization: "Bearer alice-token" },
    },
  );
  expect((await view.json()).proposal.draft.name).toBe("Unsaved edit");
});

test("player submits, GM reviews without control, then player activates and spends earned points", async ({
  page,
  browser,
  request,
}) => {
  const created = await request.post("http://127.0.0.1:8000/test-workshop", {
    headers: { Authorization: "Bearer gm-token" },
  });
  const { campaign_id } = (await created.json()) as { campaign_id: string };
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill("alice-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator(`[data-resume-id="${campaign_id}"]`).click();
  await page.getByRole("link", { name: "Character", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Submitted hero");
  await page.getByRole("button", { name: "Save and validate" }).click();
  await page.getByRole("button", { name: "Submit for GM review" }).click();
  await expect(page.getByText("Awaiting GM approval")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Activate approved draft" }),
  ).toBeDisabled();
  const context = await browser.newContext();
  try {
    const gm = await context.newPage();
    await gm.goto("http://127.0.0.1:4174/");
    await gm.getByLabel("Access token", { exact: true }).fill("gm-token");
    await gm.getByRole("button", { name: "Sign in" }).click();
    const panel = gm.getByRole("region", {
      name: `GM workshop ${campaign_id}`,
      exact: true,
    });
    await panel
      .getByRole("button", { name: "Review character submissions" })
      .click();
    await panel
      .getByRole("button", { name: "Review Submitted hero", exact: true })
      .click();
    await panel
      .getByLabel("GM review or reward reason")
      .fill("Reviewed the character concept");
    // Editing a displayed submission invalidates the GM's approval CAS token.
    await page.getByLabel("Name", { exact: true }).fill("Final reviewed hero");
    await page.getByRole("button", { name: "Save and validate" }).click();
    await expect(
      page.getByRole("button", { name: "Save and validate" }),
    ).toBeEnabled();
    await panel
      .getByRole("button", { name: "Approve submitted draft" })
      .click();
    await expect(panel.getByRole("alert")).toBeVisible();
    await page.getByRole("button", { name: "Submit for GM review" }).click();
    await panel
      .getByRole("button", { name: "Review character submissions" })
      .click();
    await panel
      .getByRole("button", { name: "Review Final reviewed hero", exact: true })
      .click();
    await panel
      .getByRole("button", { name: "Approve submitted draft" })
      .click();
    await expect(
      panel.getByRole("button", {
        name: "Review Final reviewed hero (approved)",
        exact: true,
      }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Reload saved character" }).click();
    await expect(page.getByText("GM approved this draft")).toBeVisible();
    await page.getByRole("button", { name: "Activate approved draft" }).click();
    await expect(
      page.getByRole("button", { name: "Activate approved draft" }),
    ).toBeDisabled();
    await panel
      .getByRole("button", { name: "Review character submissions" })
      .click();
    await panel.getByLabel("Reward character").selectOption("a");
    await panel.getByLabel("Earned point reward").fill("4");
    await panel.getByRole("button", { name: "Grant earned points" }).click();
    await expect(
      panel.getByRole("button", { name: "Grant earned points" }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "Reload saved character" }).click();
    await expect(page.getByText("Earned points available: 4")).toBeVisible();
    const observation = page.locator(".context-actions").filter({
      has: page.locator('select option[value="skill:observation"]:checked'),
    });
    await observation.getByRole("spinbutton").fill("8");
    await page
      .getByLabel("Approval or advancement reason")
      .fill("Observation training");
    await page.getByRole("button", { name: "Preview advancement" }).click();
    await expect(page.getByText("Cost: 4 of 4 earned points")).toBeVisible();
    await page.getByRole("button", { name: "Apply advancement" }).click();
    await expect(page.getByText("Earned points available: 0")).toBeVisible();
    const view = await request.get(
      `http://127.0.0.1:8000/campaigns/${campaign_id}/workshop/a`,
      {
        headers: { Authorization: "Bearer alice-token" },
      },
    );
    const result = await view.json();
    expect(
      result.proposal.draft.purchases.find(
        (p: { definition_id: string }) =>
          p.definition_id === "skill:observation",
      ).amount,
    ).toBe(8);
    expect(result.proposal.draft.name).toBe("Final reviewed hero");
  } finally {
    await context.close();
  }
});

test("setup party edits names, concepts and point buys before saving", async ({
  page,
  request,
}) => {
  const headers = { Authorization: "Bearer alice-token" };
  const templates = await request.get(
    "http://127.0.0.1:8000/setups/templates",
    { headers },
  );
  const [graph] = await templates.json();
  const created = await request.post("http://127.0.0.1:8000/setups", {
    headers,
    data: { id: crypto.randomUUID(), brief: graph.brief, graph },
  });
  expect(created.ok()).toBeTruthy();
  const setup = await created.json();
  await page.goto("/");
  await page.getByLabel("Access token", { exact: true }).fill("alice-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator(`[data-campaign-id="${setup.id}"]`).click();
  const editor = page.locator(".character-draft-editor").first();
  await expect(editor.locator(".point-budget")).toContainText(
    "points remaining",
  );
  await expect(
    editor.getByLabel("Start from a character template"),
  ).toBeVisible();
  await editor.getByLabel("Name", { exact: true }).fill("Party scout");
  await editor
    .getByLabel("Concept and backstory")
    .fill("A tracker searching for her brother");
  await editor.getByRole("button", { name: "Increase Strength" }).click();
  await expect(editor.locator(".purchase-cost").first()).toContainText(
    "10 pts",
  );
  await page
    .getByRole("button", { name: "Save setup draft", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("revision 1");
  const saved = await request.get(`http://127.0.0.1:8000/setups/${setup.id}`, {
    headers,
  });
  const hero = (await saved.json()).graph.actors[0].proposal.draft;
  expect(hero.name).toBe("Party scout");
  expect(hero.backstory).toBe("A tracker searching for her brother");
  expect(
    hero.purchases.find(
      (p: { definition_id: string }) => p.definition_id === "attribute:st",
    ).amount,
  ).toBe(11);
});
