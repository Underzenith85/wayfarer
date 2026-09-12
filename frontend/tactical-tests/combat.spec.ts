import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import type { TacticalSnapshot } from "../src/api/tactical";

async function fixture(
  request: APIRequestContext,
  options: { unarmed?: boolean; basic?: boolean } = {},
) {
  const response = await request.post(
    `http://127.0.0.1:8015/test-tactical?${new URLSearchParams({
      unarmed: String(options.unarmed ?? false),
      basic: String(options.basic ?? false),
    })}`,
    { headers: { Authorization: "Bearer alice-token" } },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { campaign_id: string }).campaign_id;
}
async function join(page: Page, cid: string, principal: string) {
  await page.goto(`/tactical-tests/?cid=${cid}&principal=${principal}`);
  await expect(
    page.getByRole("region", { name: "Tactical combat" }),
  ).toBeVisible();
}
async function npcWait(request: APIRequestContext, cid: string) {
  const url = `/api/tactical/v2/campaigns/${cid}`;
  const headers = { Authorization: "Bearer charlie-token" };
  await expect
    .poll(async () => {
      const latest = (await (
        await request.get(`${url}?actor_id=c`, { headers })
      ).json()) as TacticalSnapshot;
      return latest.encounters[0]?.choices.some(
        (c) =>
          c.command.kind === "take_combat_turn" &&
          c.command.maneuver === "do_nothing",
      );
    })
    .toBe(true);
  const state = (await (
    await request.get(`${url}?actor_id=c`, { headers })
  ).json()) as TacticalSnapshot;
  const choice = state.encounters[0].choices.find(
    (c) =>
      c.command.kind === "take_combat_turn" &&
      c.command.maneuver === "do_nothing",
  )!;
  const response = await request.post(`${url}/commands`, {
    headers,
    data: { command: choice.command },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

test("real API: melee, retreat, ranged attack, movement and lost-response recovery", async ({
  browser,
  request,
  baseURL,
}, testInfo) => {
  const cid = await fixture(request);
  const context = await browser.newContext({ baseURL });
  const alice = await context.newPage();
  const bob = await context.newPage();
  const leaks: string[] = [];
  alice.on("response", async (response) => {
    if (response.url().includes("/api/tactical/") && response.ok()) {
      const body = await response.text().catch(() => "");
      if (body.includes('"id":"c"') || body.includes('"target_id":"c"'))
        leaks.push(body);
    }
  });
  try {
    await join(alice, cid, "alice");
    await join(bob, cid, "bob");
    await expect(
      alice.getByRole("img", { name: /Visible tactical hex map/ }),
    ).toBeVisible();
    await alice.screenshot({
      path: testInfo.outputPath("tactical.png"),
      fullPage: true,
    });
    await alice
      .getByRole("button", { name: "Attack Iven — swing", exact: true })
      .click();
    await bob
      .getByRole("button", { name: "Dodge and retreat 0", exact: true })
      .click();
    await expect(bob.getByText(/\(you\): \(2, 0\)/)).toBeVisible();
    await bob.getByRole("button", { name: "Do Nothing", exact: true }).click();
    await npcWait(request, cid);
    await alice
      .getByRole("button", { name: "Attack Iven — throw-fixture", exact: true })
      .click();
    await bob
      .getByRole("button", { name: "None defense", exact: true })
      .click();
    await bob.getByRole("button", { name: "Do Nothing", exact: true }).click();
    await npcWait(request, cid);
    let dropped = false;
    await alice.route(
      "**/api/tactical/v2/campaigns/*/commands",
      async (route) => {
        if (!dropped) {
          dropped = true;
          await route.fetch();
          await route.abort("failed");
        } else await route.continue();
      },
    );
    const move = alice.getByRole("button", {
      name: "Move to (1, -1)",
      exact: true,
    });
    await move.focus();
    await alice.keyboard.press("Enter");
    await alice.getByRole("button", { name: "Retry same action" }).click();
    await expect(alice.getByText(/\(you\): \(1, -1\)/)).toBeVisible();
    await alice.reload();
    await expect(alice.getByText(/\(you\): \(1, -1\)/)).toBeVisible();
    await alice.getByText("Resolution traces", { exact: true }).click();
    await expect(alice.getByText(/combat.move/)).toBeVisible();
    expect(leaks).toEqual([]);
  } finally {
    await context.close();
  }
});

test("real API: grapple survives a browser reconnect and escape is server-resolved", async ({
  browser,
  request,
  baseURL,
}) => {
  const cid = await fixture(request, { unarmed: true });
  const context = await browser.newContext({ baseURL });
  try {
    const alice = await context.newPage(),
      bob = await context.newPage();
    await join(alice, cid, "alice");
    await join(bob, cid, "bob");
    await alice
      .getByRole("button", { name: "Grapple Iven (left-hand)", exact: true })
      .click();
    await bob
      .getByRole("button", { name: "None defense", exact: true })
      .click();
    await expect(
      bob.getByRole("heading", { name: "Grapple control" }),
    ).toBeVisible();
    await bob.reload();
    await expect(
      bob.getByRole("heading", { name: "Grapple control" }),
    ).toBeVisible();
    await bob
      .getByRole("button", { name: "Break Free — Mira", exact: true })
      .click();
    await bob.getByText("Resolution traces", { exact: true }).click();
    await expect(bob.getByText(/roll 9 vs/).first()).toBeVisible();
    await expect(bob.getByText(/grappled/).first()).toBeVisible();
  } finally {
    await context.close();
  }
});

test("real API: Basic combat choices, defense reconnect and hidden-actor privacy", async ({
  browser,
  request,
  baseURL,
}, testInfo) => {
  const cid = await fixture(request, { basic: true });
  const context = await browser.newContext({ baseURL });
  try {
    const alice = await context.newPage();
    const bob = await context.newPage();
    await join(alice, cid, "a");
    await join(bob, cid, "b");
    await expect(
      alice.getByRole("heading", { name: /Basic combat · round 1/ }),
    ).toBeVisible();
    await expect(
      alice.getByRole("img", { name: /Visible tactical hex map/ }),
    ).toHaveCount(0);
    await alice.screenshot({
      path: testInfo.outputPath("basic-combat.png"),
      fullPage: true,
    });
    await alice
      .getByRole("button", { name: "Attack Iven — swing", exact: true })
      .click();
    await bob.reload();
    await bob
      .getByRole("button", { name: "Dodge defense", exact: true })
      .click();
    await bob.getByRole("button", { name: "Do Nothing", exact: true }).click();
    await alice
      .getByRole("button", { name: "Withdraw Iven", exact: true })
      .click();
    await expect(alice.getByText("Mira (you): standing")).toBeVisible();
    await expect(
      alice.getByText("Iven: standing", { exact: true }),
    ).toHaveCount(0);
    await expect(
      alice.getByText(/Spatial clarification is required/),
    ).toBeVisible();
  } finally {
    await context.close();
  }
});
