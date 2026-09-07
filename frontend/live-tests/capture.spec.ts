import { test, expect, type Page } from "@playwright/test";
test("independent captive and rescuer choices survive reconnect and reunite privately", async ({
  browser,
  request,
}, testInfo) => {
  const created = await request.post("http://127.0.0.1:8000/test-campaign", {
    headers: { Authorization: "Bearer gm-token" },
  });
  expect(created.ok()).toBeTruthy();
  const { campaign_id } = (await created.json()) as { campaign_id: string };
  const options = {
    baseURL: "http://127.0.0.1:4174",
    viewport: testInfo.project.use.viewport ?? { width: 1440, height: 1000 },
    isMobile: testInfo.project.use.isMobile ?? false,
    hasTouch: testInfo.project.use.hasTouch ?? false,
  };
  const alice = await browser.newContext(options),
    bob = await browser.newContext(options);
  const a = await alice.newPage(),
    b = await bob.newPage();
  const connect = async (page: Page, principal: string) => {
    await page.goto("/");
    await page
      .getByLabel("Access token", { exact: true })
      .fill(`${principal}-token`);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.locator(`[data-resume-id="${campaign_id}"]`).click();
    await expect(
      page.getByRole("heading", { name: "Scene decisions" }),
    ).toBeVisible();
  };
  await connect(a, "alice");
  await connect(b, "bob");
  await expect(
    b.getByRole("button", { name: "rescue: rescue", exact: true }),
  ).toHaveCount(0);
  await a
    .getByRole("button", { name: "observe: observe", exact: true })
    .click();
  await expect(
    a.getByRole("button", { name: "observe: observe", exact: true }),
  ).toBeEnabled();
  await a.reload();
  await connect(a, "alice");
  await b.waitForTimeout(5500);
  await b.getByRole("button", { name: "Wait one tick", exact: true }).click();
  await expect(
    b.getByRole("button", { name: "rescue: rescue", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await b.getByRole("button", { name: "rescue: rescue", exact: true }).click();
  await expect(
    b.getByRole("button", { name: "Wait one tick", exact: true }),
  ).toBeEnabled();
  // Each view polls the shared revision; no stale action is silently rebased.
  await a.waitForTimeout(5500);
  await a.getByRole("button", { name: "assist: assist", exact: true }).click();
  await expect(
    a.getByRole("button", { name: "escape: escape", exact: true }),
  ).toHaveCount(0, { timeout: 15000 });
  await a.getByLabel("Rejoin a known group").fill("group:dock-scene");
  await a.getByRole("button", { name: "Rejoin", exact: true }).click();
  await expect(
    a.getByRole("button", { name: "Rejoin", exact: true }),
  ).toBeEnabled();
  const view = await request.get(
    `http://127.0.0.1:8000/campaigns/${campaign_id}`,
    { headers: { Authorization: "Bearer bob-token" } },
  );
  const data = (await view.json()) as {
    subgroups: { actor_ids: string[] }[];
    director: { actor_id: string }[];
  };
  expect(data.subgroups[0]?.actor_ids.sort()).toEqual(["a", "b"]);
  expect(data.director.every((t) => t.actor_id === "b")).toBeTruthy();
  await alice.close();
  await bob.close();
});
