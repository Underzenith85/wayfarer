import { test, expect, type Page } from "@playwright/test";
async function login(page: Page, player: string) {
  await page.goto("/");
  const lobby = page.getByRole("region", { name: "New game and lobby" });
  await lobby.getByLabel("Access token").fill(`${player}-token`);
  await lobby
    .getByRole("button", { name: "Load games and invitations" })
    .click();
  return lobby;
}
test("two identities activate a saved party and review speech through the live director", async ({
  browser,
  baseURL,
}, testInfo) => {
  const options = {
    baseURL: baseURL ?? "http://127.0.0.1:4174",
    viewport: testInfo.project.use.viewport ?? { width: 1440, height: 1000 },
    isMobile: testInfo.project.use.isMobile ?? false,
    hasTouch: testInfo.project.use.hasTouch ?? false,
  };
  const alice = await browser.newContext(options),
    bob = await browser.newContext(options);
  try {
    const a = await alice.newPage(),
      b = await bob.newPage();
    await a.addInitScript(() => {
      Object.defineProperty(window, "SpeechRecognition", {
        value: class {
          onresult:
            | ((e: {
                results: { isFinal: boolean; 0: { transcript: string } }[];
              }) => void)
            | null = null;
          onend: (() => void) | null = null;
          start() {
            this.onresult?.({
              results: [{ isFinal: false, 0: { transcript: "wait" } }],
            });
          }
          stop() {
            this.onend?.();
          }
          abort() {}
        },
      });
    });
    const lobby = await login(a, "alice");
    const title = `Courier ${crypto.randomUUID().slice(0, 8)}`;
    await lobby
      .getByRole("textbox", { name: "Premise", exact: true })
      .fill(title);
    await lobby.getByRole("button", { name: "Create game draft" }).click();
    await expect(
      lobby.getByRole("button", { name: "Save setup draft" }),
    ).toBeVisible();
    await lobby
      .getByLabel("Adventure and starting party")
      .selectOption("adventure");
    await lobby
      .getByRole("textbox", { name: "Premise", exact: true })
      .fill(title);
    await lobby.getByRole("button", { name: "Save setup draft" }).click();
    await lobby.getByLabel("Invite player ID").fill("bob");
    await lobby
      .getByRole("button", { name: "Invite player", exact: true })
      .click();
    await expect(lobby.getByRole("status")).toContainText("revision 2");
    const blobby = await login(b, "bob");
    // Locate the exact shared campaign through its authenticated listing.
    const values = await a.request.get("/setups", {
      headers: { Authorization: "Bearer alice-token" },
    });
    const list = (await values.json()) as {
      id: string;
      brief: { premise: string };
    }[];
    const cid = list.find((v) => v.brief.premise === title)!.id;
    await blobby.locator(`[data-campaign-id="${cid}"]`).click();
    await blobby.getByRole("button", { name: "Accept invitation" }).click();
    await expect(blobby.getByRole("status")).toContainText("revision 3");
    await lobby
      .getByRole("button", { name: "Reload games / reconcile" })
      .click();
    await lobby.getByLabel("Assign character to alice").selectOption("a");
    await expect(lobby.getByRole("status")).toContainText("revision 4");
    await lobby.getByLabel("Assign character to bob").selectOption("b");
    await expect(lobby.getByRole("status")).toContainText("revision 5");
    await lobby
      .getByRole("button", { name: "Validate and mark ready" })
      .click();
    await expect(lobby.getByRole("status")).toContainText("revision 6");
    await blobby
      .getByRole("button", { name: "Reload games / reconcile" })
      .click();
    await blobby
      .getByRole("button", { name: "Validate and mark ready" })
      .click();
    await expect(blobby.getByRole("status")).toContainText("revision 7");
    await lobby
      .getByRole("button", { name: "Reload games / reconcile" })
      .click();
    await lobby
      .getByRole("button", { name: "Start game", exact: true })
      .click();

    await expect(a.getByLabel("What do you do?")).toBeVisible();
    let requests = 0;
    a.on("request", (r) => {
      if (r.url().endsWith("/actions") && r.method() === "POST") requests++;
    });
    await a
      .getByRole("button", { name: "Start listening", exact: true })
      .click();
    await a
      .getByRole("button", { name: "Stop listening", exact: true })
      .click();
    await a
      .getByLabel("Review voice transcript", { exact: true })
      .fill("wait one minute");
    expect(requests).toBe(0);
    await a
      .getByRole("button", { name: "Send reviewed action", exact: true })
      .click();
    await expect.poll(() => requests).toBe(1);
    await expect(
      a.getByText("A moment passes.", { exact: true }),
    ).toBeVisible();
    await expect(a.getByLabel("Review voice transcript")).toHaveCount(0);
    expect(requests).toBe(1);
    const response = await b.request.get(`/campaigns/${cid}`, {
      headers: { Authorization: "Bearer bob-token" },
    });
    const view = (await response.json()) as {
      actors: string[];
      director: unknown[];
    };
    expect(view.actors).toEqual(["b"]);
    expect(view.director).toEqual([]);
  } finally {
    await alice.close();
    await bob.close();
  }
});
