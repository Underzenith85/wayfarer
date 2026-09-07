import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  providerBanner,
  providerReason,
} from "../src/presentation/availability";
async function login(page: Page, player = "alice") {
  await page.goto("/");
  await expect(
    page.getByRole("tab", { name: "New game", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Campaign ID", { exact: true })).toHaveCount(0);
  await page
    .getByLabel("Access token", { exact: true })
    .fill(`${player}-token`);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(/Signed in as/)).toContainText(player);
  return page.getByRole("region", { name: "New game and lobby" });
}
/** After a reload the tab is still signed in; no access token is asked for. */
async function resumed(page: Page, player = "alice") {
  await expect(page.getByText(/Signed in as/)).toContainText(player);
  await expect(page.getByLabel("Access token", { exact: true })).toHaveCount(0);
  return page.getByRole("region", { name: "New game and lobby" });
}
/** Setup shows one step at a time; each step is reached from its own control. */
async function step(lobby: Locator, name: string) {
  await lobby.getByRole("button", { name, exact: true }).click();
}
async function draft(page: Page, players = 1) {
  const lobby = await login(page);
  await step(lobby, "Adventure");
  await lobby
    .getByLabel("Adventure and starting party")
    .selectOption(`beacon-${players}`);
  // The steps advance with Next; only the review step creates the draft.
  await lobby.getByRole("button", { name: "Next: Rules" }).click();
  await lobby.getByRole("button", { name: "Next: Ready" }).click();
  await lobby.getByRole("button", { name: "Create game draft" }).click();
  await expect(lobby.getByRole("status")).toContainText("revision 0");
  return { lobby };
}
test("solo production entry, illegal party, stale edit, lost activation, refresh and opening action", async ({
  page,
}) => {
  const { lobby } = await draft(page);
  await expect(lobby.getByText(providerBanner.summary)).toBeVisible();
  await expect(lobby.getByText(providerBanner.disclosure)).toBeVisible();
  await step(lobby, "Concept");
  await expect(
    lobby.getByRole("button", { name: "Generate from saved brief" }),
  ).toHaveCount(0);
  await step(lobby, "Party");
  await lobby.getByLabel("Strength", { exact: true }).fill("100");
  await lobby.getByRole("button", { name: "Save setup draft" }).click();
  await lobby.getByLabel("Assign character to alice").selectOption("mira");
  await step(lobby, "Ready");
  await lobby.getByRole("button", { name: "Validate and mark ready" }).click();
  await expect(lobby.getByRole("alert")).toContainText(
    /legal|approved|Invalid/,
  );
  await expect(
    lobby.getByRole("button", { name: "Start game", exact: true }),
  ).toHaveCount(0);
  await step(lobby, "Party");
  await lobby.getByLabel("Strength", { exact: true }).fill("10");
  // Make a genuine concurrent edit using the same authenticated service.
  let cid = "";
  await page.route("**/setups/*", async (route) => {
    const request = route.request();
    if (
      request.method() !== "POST" ||
      request.postDataJSON().operation !== "edit"
    )
      return route.continue();
    cid = new URL(request.url()).pathname.split("/").at(-1)!;
    const body = request.postDataJSON();
    await page.request.post(request.url(), {
      headers: { Authorization: "Bearer alice-token" },
      data: { ...body, id: crypto.randomUUID() },
    });
    await route.continue();
  });
  await lobby.getByRole("button", { name: "Save setup draft" }).click();
  await expect(lobby.getByRole("alert")).toBeVisible();
  await page.unroute("**/setups/*");
  await lobby.getByRole("button", { name: "Reload games / reconcile" }).click();
  await lobby.getByLabel("Assign character to alice").selectOption("mira");
  await step(lobby, "Ready");
  await lobby.getByRole("button", { name: "Validate and mark ready" }).click();
  await expect(
    lobby.getByRole("button", { name: "Start game", exact: true }),
  ).toBeVisible();
  let original: string | null = null;
  await page.route(`**/setups/${cid}`, async (route) => {
    if (
      route.request().method() === "POST" &&
      route.request().postDataJSON().operation === "activate"
    ) {
      original = route.request().postData();
      await route.fetch(); // commit at the real server, then lose only the acknowledgement
      await route.abort();
    } else await route.continue();
  });
  await lobby.getByRole("button", { name: "Start game", exact: true }).focus();
  await page.keyboard.press("Enter");
  // A pending command exists while the original request is still in flight.
  // Wait for the injected loss to reach the UI before removing its route.
  await expect(lobby.getByRole("alert")).toBeVisible();
  await expect(
    lobby.getByRole("button", { name: "Retry original setup request" }),
  ).toBeEnabled();
  await page.unroute(`**/setups/${cid}`);
  await page.reload();
  await resumed(page);
  const retry = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().endsWith(`/setups/${cid}`),
  );
  await page
    .getByRole("button", { name: "Retry original setup request" })
    .click();
  expect((await retry).postData()).toBe(original);
  await expect(
    page.getByRole("heading", { name: "Stormbound Harbor" }),
  ).toBeVisible();
  // The play shell states the missing provider once, the composer states only
  // the part that applies to it, and no scene action the engine would reject is
  // offered as a control.
  await expect(page.getByText(providerBanner.summary)).toHaveCount(1);
  await expect(page.getByLabel("What do you do?")).toBeDisabled();
  await expect(page.getByText(providerReason.text)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Inspect Stormbound Harbor" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Wait one tick", exact: true })
    .click();
  await page.getByText("Rolls and consequences", { exact: true }).click();
  await expect(
    page.getByText("Game time: 1 ticks", { exact: true }),
  ).toBeVisible();
  await page.reload();
  // The open campaign and its route survive the refresh, with no sign-in.
  await expect(page).toHaveURL(new RegExp(`/c/${cid}$`));
  await expect(page.getByLabel("Access token", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Stormbound Harbor" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Travel to The Beacon", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "The Beacon", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("listitem")
    .filter({ hasText: "Travel to The Beacon" })
    .getByText("Rolls and consequences", { exact: true })
    .click();
  await expect(
    page.getByText("Game time: 3 ticks", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Session ended" }),
  ).toHaveCount(0);
});

test("separate invited identity joins, readies and starts without leaking a private setup", async ({
  page,
  browser,
  baseURL,
}) => {
  const { lobby } = await draft(page, 2);
  await lobby.getByLabel("Invite player ID").fill("bob");
  const [invitation] = await Promise.all([
    page.waitForResponse((response) => {
      const request = response.request();
      return (
        request.method() === "POST" &&
        /^\/setups\/[^/]+$/.test(new URL(request.url()).pathname) &&
        request.postDataJSON().operation === "invite"
      );
    }),
    lobby.getByRole("button", { name: "Invite player", exact: true }).click(),
  ]);
  expect(invitation.ok()).toBeTruthy();
  const { id: cid } = await invitation.json();
  await expect(lobby.getByRole("status")).toContainText("revision 1");
  const guest = await browser.newPage({
    baseURL: baseURL ?? "http://127.0.0.1:4180",
    viewport: page.viewportSize(),
  });
  try {
    const invited = await login(guest, "bob");
    const privateRead = await guest.request.get(`/setups/${cid}`, {
      headers: { Authorization: "Bearer eve-token" },
    });
    expect(privateRead.status()).toBe(404);
    const own = await guest.request.get(`/setups/${cid}`, {
      headers: { Authorization: "Bearer bob-token" },
    });
    expect(own.ok()).toBeTruthy();
    expect((await own.json()).graph).toBeNull();
    await invited.locator(`[data-campaign-id="${cid}"]`).click();
    await invited.getByRole("button", { name: "Accept invitation" }).click();
    await expect(invited.getByRole("status")).toContainText("revision 2");
    await lobby
      .getByRole("button", { name: "Reload games / reconcile" })
      .click();
    await lobby.getByLabel("Assign character to alice").selectOption("mira");
    await expect(lobby.getByRole("status")).toContainText("revision 3");
    await lobby.getByLabel("Assign character to bob").selectOption("iven");
    await expect(lobby.getByRole("status")).toContainText("revision 4");
    await step(lobby, "Ready");
    await lobby
      .getByRole("button", { name: "Validate and mark ready" })
      .click();
    await expect(lobby.getByRole("status")).toContainText("revision 5");
    await invited
      .getByRole("button", { name: "Reload games / reconcile" })
      .click();
    await invited
      .getByRole("button", { name: "Validate and mark ready" })
      .click();
    await expect(invited.getByRole("status")).toContainText("revision 6");
    await lobby
      .getByRole("button", { name: "Reload games / reconcile" })
      .click();
    await lobby
      .getByRole("button", { name: "Start game", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Stormbound Harbor" }),
    ).toBeVisible();
    await invited
      .getByRole("button", { name: "Reload games / reconcile" })
      .click();
    await invited.getByRole("button", { name: "Open playing scene" }).click();
    await expect(
      guest.getByRole("heading", { name: "Stormbound Harbor" }),
    ).toBeVisible();
  } finally {
    await guest.close();
  }
});
