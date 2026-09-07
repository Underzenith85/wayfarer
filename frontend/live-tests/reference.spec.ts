import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

async function start(request: APIRequestContext) {
  const templates = await request.get("/setups/templates", {
    headers: { Authorization: "Bearer alice-token" },
  });
  const graph = (
    (await templates.json()) as { id: string; brief: object }[]
  ).find((g) => g.id === "last-lantern-1")!;
  const created = await request.post("/setups", {
    headers: { Authorization: "Bearer alice-token" },
    data: { id: crypto.randomUUID(), brief: graph.brief },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = (await created.json()) as { id: string };
  const commands = [
    ["alice", { operation: "edit", graph }],
    ["alice", { operation: "invite", principal_id: "bob" }],
    ["bob", { operation: "join" }],
    ["alice", { operation: "assign", principal_id: "alice", actor_ids: ["a"] }],
    ["alice", { operation: "assign", principal_id: "bob", actor_ids: ["b"] }],
    ["alice", { operation: "ready" }],
    ["bob", { operation: "ready" }],
    ["alice", { operation: "activate" }],
  ] as const;
  for (const [revision, [principal, fields]] of commands.entries()) {
    const result = await request.post(`/setups/${id}`, {
      headers: { Authorization: `Bearer ${principal}-token` },
      data: { id: crypto.randomUUID(), expected_revision: revision, ...fields },
    });
    expect(result.ok(), await result.text()).toBeTruthy();
  }
  return id;
}
async function login(page: Page, principal: string, id: string) {
  await page.goto("/");
  // A tab that has signed in before restores its session and reopens its
  // campaign; only a fresh tab is asked for the access token.
  const remembered = await page.evaluate(
    () => sessionStorage.getItem("wayfarer:session") !== null,
  );
  const lobby = page.getByRole("region", { name: "New game and lobby" });
  if (remembered) {
    // The tab reopened its campaign in the play shell; setup is behind Session.
    await page.getByRole("button", { name: "Session", exact: true }).click();
    await page
      .getByRole("dialog", { name: "Session" })
      .getByRole("button", { name: "Switch campaign", exact: true })
      .click();
  } else {
    await lobby.getByLabel("Access token").fill(`${principal}-token`);
    await lobby.getByRole("button", { name: "Sign in" }).click();
  }
  await lobby.locator(`[data-campaign-id="${id}"]`).click();
  // Joining loads the snapshot and its scene in separate requests. Do not let
  // another player mutate the campaign until this player's join has completed.
  await expect(
    page.getByRole("region", { name: "Scene decisions" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Wait one tick", exact: true }),
  ).toBeEnabled();
  return lobby;
}

test("reference adventure: reviewed voice, negotiation, saved epilogue and successor", async ({
  browser,
  request,
  baseURL,
}) => {
  const id = await start(request);
  const alice = await browser.newContext({
      baseURL: baseURL ?? "http://127.0.0.1:4174",
    }),
    bob = await browser.newContext({
      baseURL: baseURL ?? "http://127.0.0.1:4174",
    });
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
              results: [
                { isFinal: false, 0: { transcript: "inspect the manifest" } },
              ],
            });
          }
          stop() {
            this.onend?.();
          }
          abort() {}
        },
      });
      // The browser-speech notice is disclosed on first use of the mic; this
      // test exercises the capture past it.
      localStorage.setItem("wayfarer-voice-notice", "seen");
    });
    await login(a, "alice", id);
    await login(b, "bob", id);
    await expect(
      a.getByRole("heading", { name: "Scene decisions" }),
    ).toBeVisible();
    let writes = 0;
    a.on("request", (r) => {
      if (r.method() === "POST" && r.url().endsWith("/actions")) writes++;
    });
    await a.getByRole("button", { name: "Start voice input" }).click();
    await a.getByRole("button", { name: "Stop voice input" }).click();
    expect(writes).toBe(0);
    await a
      .getByRole("button", { name: "Send reviewed action", exact: true })
      .click();
    await expect.poll(() => writes).toBe(1);
    await expect(
      a.getByText("Your choice has been recorded.", { exact: true }),
    ).toBeVisible();
    await a.getByRole("button", { name: "Refresh scene decisions" }).click();
    await a.getByRole("button", { name: "Begin: parley", exact: true }).click();
    await expect(
      a.getByRole("button", { name: "appeal-to-duty", exact: true }),
    ).toBeVisible();
    // Reload the actual browser at the persisted decision; no choice is invented.
    await login(a, "alice", id);
    await a
      .getByRole("button", { name: "appeal-to-duty", exact: true })
      .click();
    await expect(
      a.getByRole("status").filter({ hasText: "Adventure success" }),
    ).toBeVisible();
    const privateView = await b.request.get(`/campaigns/${id}`, {
      headers: { Authorization: "Bearer bob-token" },
    });
    expect(await privateView.text()).not.toContain("secretly owes");
    const conclusion = await login(a, "alice", id);
    // Setup is reached from the play header; it reopens the played campaign.
    await a.getByRole("button", { name: "Session", exact: true }).click();
    await a
      .getByRole("dialog", { name: "Session" })
      .getByRole("button", { name: "Switch campaign", exact: true })
      .click();
    await conclusion
      .getByRole("button", { name: "End campaign", exact: true })
      .click();
    await expect(
      conclusion.getByRole("article", { name: "Adventure conclusion" }),
    ).toContainText("success");
    await conclusion
      .getByRole("button", { name: "Generate next-adventure preview" })
      .click();
    await expect(
      conclusion.getByRole("article", { name: "Next adventure preview" }),
    ).toContainText("A Favor Repaid");
    await conclusion
      .getByRole("button", { name: "Continue to next adventure", exact: true })
      .click();
    await expect(conclusion.getByRole("status")).toContainText(
      "A Favor Repaid · In play",
    );
    await expect(
      conclusion.getByRole("article", { name: "Adventure conclusion" }),
    ).toContainText("The Last Lantern");
  } finally {
    await alice.close();
    await bob.close();
  }
});

test("reference rescue: separate players coordinate, reconnect, reclaim gear and reunite", async ({
  browser,
  request,
  baseURL,
}) => {
  const id = await start(request);
  const alice = await browser.newContext({
    baseURL: baseURL ?? "http://127.0.0.1:4174",
  });
  const bob = await browser.newContext({
    baseURL: baseURL ?? "http://127.0.0.1:4174",
  });
  const read = async (principal: string) => {
    const result = await request.get(`/campaigns/${id}`, {
      headers: { Authorization: `Bearer ${principal}-token` },
    });
    expect(result.ok()).toBeTruthy();
    return result.json();
  };
  const choose = async (page: Page, name: string) => {
    const refresh = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/campaigns/${id}`) && r.request().method() === "GET",
    );
    await page.getByRole("button", { name: "Refresh scene decisions" }).click();
    await refresh;
    const submitted = page.waitForResponse(
      (r) => r.url().endsWith("/commands") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name, exact: true }).click();
    expect((await submitted).ok()).toBeTruthy();
    await expect(
      page.getByRole("button", { name: "Wait one tick", exact: true }),
    ).toBeEnabled();
  };
  try {
    const a = await alice.newPage(),
      b = await bob.newPage();
    await login(a, "alice", id);
    await login(b, "bob", id);
    await choose(a, "Split from group");
    const captured = await request.post(`/campaigns/${id}/commands`, {
      headers: { Authorization: "Bearer gm-token" },
      data: {
        id: crypto.randomUUID(),
        actor_id: "gm",
        expected_revision: (await read("gm")).revision,
        kind: "apply_setback",
        rule_id: "harbor-capture",
        target_actor_id: "a",
      },
    });
    expect(captured.ok()).toBeTruthy();
    expect((await read("alice")).inventory).toEqual([]);
    await expect(
      b.getByRole("button", { name: "rescue: quiet-rescue", exact: true }),
    ).toHaveCount(0);
    await choose(a, "observe: study-cell");
    await choose(b, "Wait one tick");
    await choose(a, "communicate: signal-outside");
    await choose(b, "Wait one tick");
    await choose(b, "rescue: quiet-rescue");
    // The captive reconnects to their persisted private choices while rescue is pending.
    await login(a, "alice", id);
    expect((await read("alice")).game_time).toBe(2);
    await choose(a, "assist: loosen-bars");
    await choose(a, "recover_items: recover-gear");
    await choose(b, "Wait one tick");
    await a.getByLabel("Rejoin a known group").fill("group:harbor-scene");
    await choose(a, "Rejoin");
    const final = await read("alice");
    expect(final.subgroups).toHaveLength(1);
    expect(
      final.inventory.some((item: { id: string }) => item.id === "blade-a"),
    ).toBeTruthy();
    expect(final.objectives.outcome).toBe("success");
    expect(JSON.stringify(await read("bob"))).not.toContain("loose shutter");
  } finally {
    await alice.close();
    await bob.close();
  }
});
