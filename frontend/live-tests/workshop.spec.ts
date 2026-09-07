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
  await page
    .getByRole("button", { name: "Load games and invitations" })
    .click();
  await page.locator(`[data-resume-id="${campaign_id}"]`).click();
  await page.getByRole("link", { name: "Character", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Character workshop" }),
  ).toBeVisible();
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
