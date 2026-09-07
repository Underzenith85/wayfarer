import { test, expect } from "@playwright/test";
async function open(page: import("@playwright/test").Page, room: string) {
  await page.goto(`/campaign?adventure=true&room=${room}`);
  await page.getByRole("button", { name: /^(Open|Resume) campaign$/ }).click();
  await expect(
    page.getByRole("region", { name: "Encounter", exact: true }),
  ).toBeVisible();
}
async function openClosure(
  page: import("@playwright/test").Page,
  room: string,
  journey: "success" | "partial" | "failure" | "continue" | "archive",
) {
  await page.goto(`/campaign?adventure=true&closure=${journey}&room=${room}`);
  await page.getByRole("button", { name: /^(Open|Resume) campaign$/ }).click();
  await page.getByRole("link", { name: "Campaign", exact: true }).click();
}
test("defense, resumed noncombat decisions, objectives and searchable discoveries", async ({
  page,
}) => {
  const room = crypto.randomUUID();
  await open(page, room);
  await expect(
    page.getByRole("button", { name: "Raise shield", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Target for Raise shield").selectOption("guard");
  await page.getByText("Rules trace: Raise shield", { exact: true }).click();
  await expect(page.getByText(/Shield ready/)).toBeVisible();
  await page.getByRole("button", { name: "Raise shield", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "social · Round 2" }),
  ).toBeVisible();
  await open(page, room);
  await expect(
    page.getByRole("heading", { name: "social · Round 2" }),
  ).toBeVisible();
  for (const label of [
    "Offer safe passage",
    "Examine the seal",
    "Move through cover",
    "Secure the rope",
  ]) {
    await page.getByLabel(`Target for ${label}`).selectOption("route");
    await page.getByRole("button", { name: label, exact: true }).click();
  }
  await expect(
    page.getByRole("heading", { name: "No pending encounter decision" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Journal", exact: true }).click();
  await expect(
    page.getByText("5 of 5 known steps", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("Secured the bridge. Escape route complete.", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel("Search discoveries").fill("marked");
  await page.getByLabel("Discovery type").selectOption("clue");
  await page.getByRole("button", { name: "Marked route", exact: true }).click();
  await page.getByRole("link", { name: "Open permanent entry link" }).click();
  await expect(
    page.getByRole("heading", { name: "Marked route", exact: true }),
  ).toBeVisible();
});
test("hidden entries and another character's deep links reveal no content", async ({
  page,
}) => {
  const room = crypto.randomUUID();
  await page.goto(
    `/journal?adventure=true&room=${room}&campaign=campaign-1&actor=hero-1&entry=route-hero-1`,
  );
  await expect(
    page.getByText("Journal entry unavailable.", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Search discoveries").fill("marked");
  await expect(page.getByText("0 known entries")).toBeVisible();
  await page.goto(
    `/journal?adventure=true&room=${room}&multiplayer=rescuer&campaign=campaign-1&actor=hero-2&entry=place-hero-1`,
  );
  await expect(
    page.getByText("Journal entry unavailable.", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).not.toContainText("copper finch");
});

test("lost decision acknowledgement retries once and recap resumes after its checkpoint", async ({
  page,
}) => {
  const room = crypto.randomUUID();
  await open(page, room);
  let lost = false;
  await page.route("**/__fixtures/multiplayer", async (route) => {
    const body = route.request().postDataJSON() as { op: string };
    if (body.op === "adventure-decide" && !lost) {
      lost = true;
      await route.fetch();
      await route.abort("failed");
    } else await route.continue();
  });
  await page.getByLabel("Target for Raise shield").selectOption("guard");
  await page.getByRole("button", { name: "Raise shield", exact: true }).click();
  await page.getByRole("button", { name: "Retry recorded decision" }).click();
  await expect(
    page.getByRole("heading", { name: "social · Round 2" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Journal", exact: true }).click();
  await expect(
    page.getByText("Deflected the guard’s strike.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Mark recap read for next visit" })
    .click();
  await page.getByRole("link", { name: "Play", exact: true }).click();
  await page.getByLabel("Target for Offer safe passage").selectOption("route");
  await page
    .getByRole("button", { name: "Offer safe passage", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "investigation · Round 3" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Journal", exact: true }).click();
  await expect(
    page.getByText("Promised the witness safe passage.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Deflected the guard’s strike.", { exact: true }),
  ).toHaveCount(0);
});

test("partial closure settles offered downtime once and continues the campaign", async ({
  page,
}) => {
  const room = crypto.randomUUID();
  await openClosure(page, room, "partial");
  await expect(
    page.getByRole("heading", { name: "Adventure complete" }),
  ).toBeVisible();
  await expect(
    page.getByText("partial outcome", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/not the end of the campaign/)).toBeVisible();
  await expect(
    page.getByText("2 points authorized by the campaign record"),
  ).toBeVisible();
  await page.getByLabel("Research").check();
  await page
    .getByRole("button", { name: "Settle rewards and choices" })
    .click();
  await expect(
    page.getByText(/This settlement cannot be claimed again/),
  ).toBeVisible();
  await openClosure(page, room, "partial");
  await expect(page.getByLabel("Research")).toBeChecked();
  await expect(
    page.getByText(/This settlement cannot be claimed again/),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Continue campaign" }),
  ).toBeVisible();
});

test("archived success is final and has no continuation action", async ({
  page,
}) => {
  await openClosure(page, crypto.randomUUID(), "archive");
  await expect(page.getByText(/Campaign archived/)).toBeVisible();
  await expect(
    page.getByText("success outcome", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Continue campaign" }),
  ).toHaveCount(0);
});

test("success, failure and continuing failure remain distinct and spoiler-safe", async ({
  page,
}) => {
  for (const journey of ["success", "failure", "continue"] as const) {
    await openClosure(page, crypto.randomUUID(), journey);
    const success = journey === "success";
    await expect(
      page.getByText(success ? "success outcome" : "failure outcome", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.locator("body")).not.toContainText("copper finch");
    if (success)
      await expect(
        page.getByText(/2 points authorized by the campaign record/),
      ).toBeVisible();
    else
      await expect(
        page.getByText("No rewards were authorized for this outcome.", {
          exact: true,
        }),
      ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Continue campaign" }),
    ).toHaveCount(success ? 0 : 1);
  }
});
