import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";

async function open(page: Page, identity: "captive" | "rescuer", room: string) {
  await page.goto(`/campaign?multiplayer=${identity}&room=${room}`);
  await page
    .getByRole("button", { name: "Open campaign", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: identity === "captive" ? "The locked cell" : "The woodland trail",
      exact: true,
    }),
  ).toBeVisible();
}
async function scenario(
  request: APIRequestContext,
  room: string,
  identity: string,
  kind: string,
) {
  const response = await request.post("/__fixtures/multiplayer", {
    headers: { "x-mock-room": room, "x-mock-identity": identity },
    data: { op: "scenario", scenario: kind },
  });
  expect(response.ok()).toBe(true);
}
async function move(page: Page, kind: "split" | "transfer" | "rejoin") {
  await page.getByText("Move with your group", { exact: true }).click();
  await page.getByLabel("Group operation", { exact: true }).selectOption(kind);
  await page
    .getByRole("button", { name: `Confirm ${kind}`, exact: true })
    .click();
}
test("two identities keep captive decisions private, reconnect, and reunite without sharing secrets", async ({
  browser,
  page,
  request,
}) => {
  const room = crypto.randomUUID();
  const other = await browser.newContext({
    baseURL: "http://127.0.0.1:4173",
    viewport: page.viewportSize(),
  });
  const captive = await other.newPage();
  try {
    const payloads: string[] = [];
    page.on("response", (response) => {
      if (response.url().includes("/__fixtures/multiplayer"))
        void response
          .text()
          .then((text) => payloads.push(text))
          .catch(() => {});
    });
    await open(captive, "captive", room);
    await open(page, "rescuer", room);
    await expect(page.locator("body")).not.toContainText("copper finch");
    await expect(captive.locator("body")).not.toContainText("alder");
    await expect(page.getByLabel("Controlled character")).not.toContainText(
      "Mara",
    );
    await captive.getByLabel("What do you do?").fill("Examine the lock");
    await captive
      .getByRole("button", { name: "Send action", exact: true })
      .click();
    await expect(
      captive.getByRole("region", { name: "Pending choice" }),
    ).toBeVisible();
    await page.getByLabel("What do you do?").fill("Follow the trail");
    await page
      .getByRole("button", { name: "Send action", exact: true })
      .click();
    await expect(page.getByText("Committed", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Pending choice" }),
    ).toHaveCount(0);
    await page
      .getByLabel("What do you do?")
      .fill("A plan kept through disconnect");
    await page.context().setOffline(true);
    await expect(
      page.getByRole("button", { name: "Send action", exact: true }),
    ).toBeDisabled();
    await scenario(request, room, "rescuer", "missed");
    await page.context().setOffline(false);
    await expect(
      page.getByRole("heading", { name: "The woodland trail", exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("What do you do?")).toHaveValue(
      "A plan kept through disconnect",
    );
    await expect(
      page.getByRole("button", { name: "Send action", exact: true }),
    ).toBeEnabled();
    const clarificationResponse = captive.waitForResponse(
      (response) =>
        response.url().includes("/__fixtures/multiplayer") &&
        response.request().postDataJSON()?.op === "clarify",
    );
    await captive
      .getByRole("button", { name: "Wait for the guard", exact: true })
      .click();
    const clarified = await clarificationResponse;
    expect(clarified.ok(), await clarified.text()).toBe(true);
    await expect(captive.getByText("Committed", { exact: true })).toBeVisible();
    const before = await page
      .locator(".multiplayer-panel")
      .getAttribute("data-checkpoint");
    const captiveBefore = await captive
      .locator(".multiplayer-panel")
      .getAttribute("data-checkpoint");
    await scenario(request, room, "rescuer", "rescue");
    await expect(page.locator(".multiplayer-panel")).not.toHaveAttribute(
      "data-checkpoint",
      before!,
    );
    await expect(captive.locator(".multiplayer-panel")).not.toHaveAttribute(
      "data-checkpoint",
      captiveBefore!,
    );
    await move(page, "rejoin");
    await expect(
      page.getByRole("heading", { name: "The rendezvous", exact: true }),
    ).toBeVisible();
    await move(captive, "rejoin");
    await expect(
      captive.getByRole("heading", { name: "The rendezvous", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".presence-list")).toContainText("Mara’s player");
    await expect(page.locator("body")).not.toContainText("copper finch");
    await expect(captive.locator("body")).not.toContainText("alder");
    expect(payloads.join("\n")).not.toContain("copper finch");
  } finally {
    await other.close();
  }
});
test("owned perspectives isolate drafts and group commands follow permitted destinations", async ({
  page,
}) => {
  const room = crypto.randomUUID();
  await open(page, "rescuer", room);
  await page.getByLabel("What do you do?").fill("Ivo's private draft");
  await page.getByLabel("Controlled character").selectOption("hero-3");
  await expect(
    page.getByRole("heading", { name: "The old watchpost", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("What do you do?")).toHaveValue("");
  await page.getByLabel("Controlled character").selectOption("hero-2");
  await expect(page.getByLabel("What do you do?")).toHaveValue(
    "Ivo's private draft",
  );
  await move(page, "split");
  await expect(
    page.getByRole("heading", { name: "The old watchpost", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("What do you do?")).toHaveValue("");
  await move(page, "transfer");
  await expect(
    page.getByRole("heading", { name: "The woodland trail", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("What do you do?")).toHaveValue(
    "Ivo's private draft",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("readiness and optional OOC stay separate from scene actions", async ({
  page,
}) => {
  await open(page, "rescuer", crypto.randomUUID());
  await page.getByRole("button", { name: "Mark ready", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Mark not ready", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Show out-of-character table chat").check();
  await page
    .getByLabel("Table message", { exact: true })
    .fill("Need a short break");
  await page
    .getByRole("button", { name: "Send table message", exact: true })
    .click();
  await expect(
    page.getByLabel("Show out-of-character table chat"),
  ).not.toBeChecked();
  await page.getByLabel("Show out-of-character table chat").check();
  await expect(
    page.getByRole("region", { name: "Out-of-character table chat" }),
  ).toContainText("Need a short break");
  await expect(page.locator(".transcript-entry")).toHaveCount(0);
});
for (const kind of ["revoke", "reassign"])
  test(`${kind} clears private state after disconnect`, async ({
    page,
    request,
  }) => {
    const room = crypto.randomUUID();
    await open(page, "captive", room);
    await page.getByLabel("What do you do?").fill("Private captive plan");
    await page.context().setOffline(true);
    await scenario(request, room, "captive", kind);
    await page.context().setOffline(false);
    await expect(
      page.getByRole("heading", { name: "Session ended", exact: true }),
    ).toBeVisible();
    await expect(page.locator("body")).not.toContainText("copper finch");
    await expect(page.locator("body")).not.toContainText(
      "Private captive plan",
    );
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter((k) =>
          k.startsWith("wayfarer:draft:"),
        ),
      ),
    ).toEqual([]);
  });

test("lost acknowledgement reconciles the accepted action without a second submission", async ({
  page,
}) => {
  await open(page, "rescuer", crypto.randomUUID());
  let submissions = 0;
  await page.route("**/__fixtures/multiplayer", async (route) => {
    if (route.request().postDataJSON().op !== "submit") {
      await route.continue();
      return;
    }
    submissions++;
    await route.fetch(); // Authority commits; the client never receives this acknowledgement.
    await route.abort("failed");
  });
  await page.getByLabel("What do you do?").fill("Continue along the trail");
  await page.getByRole("button", { name: "Send action", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Scene connection" }),
  ).toContainText("Disconnected");
  await page
    .getByRole("button", { name: "Reconnect scene", exact: true })
    .click();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  await expect(page.locator(".transcript-entry")).toHaveCount(1);
  expect(submissions).toBe(1);
});
