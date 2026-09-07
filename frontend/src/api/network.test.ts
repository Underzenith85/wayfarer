// @vitest-environment node
import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import { setupServer } from "msw/node";
import { createMockHandlers } from "../mocks/handlers";
import {
  fixtureCommand,
  fixtureCredential,
  type Scenario,
} from "../mocks/catalog";
import { NetworkPlayTransport } from "./play-transport";
import { createApiClient } from "./client";
import { liveMessages } from "./live";
import type { ServerMessage } from "./events.generated";
const origin = "http://wayfarer.test";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
function setup(scenario: Scenario = "resolve", principal = "player-1") {
  const mock = createMockHandlers({ scenario, origin, latency: 1 });
  server.use(...mock.handlers);
  const transport = new NetworkPlayTransport({
    origin,
    credential: fixtureCredential(principal),
    principalId: principal,
    sample: true,
  });
  return { mock, transport };
}
const signal = () => AbortSignal.timeout(5000);
async function finish(transport: NetworkPlayTransport, id: string) {
  await transport.getAction("campaign-1", id, signal());
  return transport.getAction("campaign-1", id, signal());
}
async function* stream(
  credential = fixtureCredential("player-1"),
  resume?: { cursor: string; visibility_epoch: string },
) {
  yield* liveMessages({
    url: "ws://wayfarer.test/api/v1/live",
    credential,
    scope: {
      campaign_id: "campaign-1",
      scene_id: "cellar-1",
      actor_id: "hero-1",
    },
    signal: signal(),
    ...(resume ? { resume } : {}),
  });
}
describe("one generated network boundary for real and mocked transports", () => {
  it("loads atomic WS snapshot and plays a scripted item-use journey through HTTP", async () => {
    const { transport } = setup();
    expect(await transport.listCampaigns(signal())).toHaveLength(2);
    const before = await transport.readSnapshot("campaign-1", signal());
    expect(before.inventories[0]!.items[0]!.quantity).toBe(2);
    const action = await transport.submitAction(
      "campaign-1",
      fixtureCommand,
      signal(),
    );
    expect((await finish(transport, action.id)).status).toBe("succeeded");
    const after = await transport.readSnapshot("campaign-1", signal());
    expect(after.inventories[0]!.items[0]!.quantity).toBe(1);
    const chunks = [];
    for await (const chunk of transport.narrate(
      "campaign-1",
      action.id,
      signal(),
    ))
      chunks.push(chunk);
    expect(chunks.at(-1)?.status).toBe("complete");
  });
  it("handles lost acknowledgement and changed-payload retries without duplicate actions", async () => {
    const { transport } = setup("retry");
    await expect(
      transport.submitAction("campaign-1", fixtureCommand, signal()),
    ).rejects.toThrow();
    const action = await transport.submitAction(
      "campaign-1",
      fixtureCommand,
      signal(),
    );
    await finish(transport, action.id);
    const replay = await transport.submitAction(
      "campaign-1",
      fixtureCommand,
      signal(),
    );
    expect(replay.status).toBe("succeeded");
    expect(await transport.listActions("campaign-1", signal())).toHaveLength(1);
    await expect(
      transport.submitAction(
        "campaign-1",
        { ...fixtureCommand, intent: { kind: "wait", ticks: 1 } },
        signal(),
      ),
    ).rejects.toMatchObject({ code: "idempotency_conflict" });
  });
  it("continues the original action after a clarification and rejects cancellation after commit", async () => {
    const { transport } = setup("clarify");
    const action = await transport.submitAction(
      "campaign-1",
      fixtureCommand,
      signal(),
    );
    const pending = await transport.getAction(
      "campaign-1",
      action.id,
      signal(),
    );
    expect(pending.status).toBe("needs_clarification");
    if (pending.status !== "needs_clarification")
      throw new Error("Expected clarification");
    const resumed = await transport.clarifyAction(
      "campaign-1",
      action.id,
      {
        command_id: "22222222-2222-4222-8222-222222222222",
        expected_action_version: pending.version,
        expected_versions: fixtureCommand.expected_versions,
        clarification_id: pending.clarification.id,
        answer: { choice_id: "door-1" },
      },
      signal(),
    );
    expect(resumed.id).toBe(action.id);
    const done = await finish(transport, action.id);
    const cancel = await transport.api.POST(
      "/campaigns/{campaign_id}/actions/{action_id}/cancellation",
      {
        params: { path: { campaign_id: "campaign-1", action_id: action.id } },
        body: {
          command_id: "33333333-3333-4333-8333-333333333333",
          expected_action_version: done.version,
        },
      },
    );
    expect(cancel.error?.code).toBe("invalid_transition");
  });
  it("uses server-shaped errors for stale versions, auth, hidden actors and invalid input", async () => {
    const { transport } = setup("stale");
    await expect(
      transport.submitAction("campaign-1", fixtureCommand, signal()),
    ).rejects.toMatchObject({ code: "stale_version" });
    const bad = createApiClient(`${origin}/api/v1`, "bad");
    expect((await bad.GET("/me")).error?.code).toBe("unauthenticated");
    const hidden = await transport.api.GET(
      "/campaigns/{campaign_id}/characters/{actor_id}",
      {
        params: { path: { campaign_id: "campaign-1", actor_id: "rescuer-1" } },
      },
    );
    expect(hidden.response.status).toBe(404);
    const response = await fetch(
      `${origin}/api/v1/campaigns/campaign-1/actions`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${fixtureCredential("player-1")}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ...fixtureCommand, hp: 100 }),
      },
    );
    expect(response.status).toBe(400);
  });
  it("paginates through the generated client and returns join receipts", async () => {
    const { transport } = setup("join");
    const first = await transport.api.GET("/campaigns", {
      params: { query: { limit: 1 } },
    });
    expect(first.data?.items).toHaveLength(1);
    const second = await transport.api.GET("/campaigns", {
      params: { query: { limit: 1, cursor: first.data!.next_cursor! } },
    });
    expect(second.data?.items[0]?.id).toBe("campaign-2");
    const member = await transport.api.POST("/invitations/redeem", {
      body: {
        command_id: fixtureCommand.command_id,
        token: "fixture-invitation-token".padEnd(40, "_"),
      },
    });
    expect(member.data?.campaign_id).toBe("campaign-1");
  });
  it("replays from a checkpoint and resets an expired cursor", async () => {
    setup();
    let ready: Extract<ServerMessage, { type: "stream.ready" }> | undefined;
    for await (const message of stream()) {
      if (message.type === "stream.ready") {
        ready = message;
        break;
      }
    }
    const frames: ServerMessage[] = [];
    for await (const message of stream(fixtureCredential("player-1"), {
      cursor: ready!.cursor,
      visibility_epoch: ready!.visibility_epoch,
    })) {
      frames.push(message);
      if (message.type === "stream.ready") break;
    }
    expect(frames.some((x) => x.type === "snapshot.begin")).toBe(false);
    expect(frames[0]).toMatchObject({ type: "subscribed", mode: "replay" });
    for await (const message of stream(fixtureCredential("player-1"), {
      cursor: "expired_cursor".padEnd(40, "_"),
      visibility_epoch: ready!.visibility_epoch,
    })) {
      expect(message.type).toBe("stream.reset");
      break;
    }
  });
  it("replays a retained invalidation after a scripted disconnect", async () => {
    const { transport } = setup("reconnect");
    let ready: Extract<ServerMessage, { type: "stream.ready" }> | undefined;
    const first = async () => {
      for await (const frame of stream())
        if (frame.type === "stream.ready") ready = frame;
    };
    await expect(first()).rejects.toThrow("1013");
    const frames: ServerMessage[] = [];
    for await (const frame of stream(fixtureCredential("player-1"), {
      cursor: ready!.cursor,
      visibility_epoch: ready!.visibility_epoch,
    })) {
      frames.push(frame);
      if (frame.type === "stream.ready") break;
    }
    expect(frames.map((x) => x.type)).toEqual([
      "subscribed",
      "projection.invalidated",
      "stream.ready",
    ]);
    expect(frames[1]).toMatchObject({ previous_cursor: ready!.cursor });
    expect(
      (await transport.readSnapshot("campaign-1", signal())).inventories[0]!
        .version,
    ).toBe("i2");
  });
  it.each(["duplicate", "out-of-order"] as const)(
    "delivers deterministic %s fault frames",
    async (scenario) => {
      setup(scenario);
      const changes: Extract<
        ServerMessage,
        { type: "projection.invalidated" }
      >[] = [];
      for await (const frame of stream()) {
        if (frame.type === "projection.invalidated") {
          changes.push(frame);
          if (scenario === "out-of-order" || changes.length === 2) break;
        }
      }
      if (scenario === "duplicate") expect(changes[0]).toEqual(changes[1]);
      else expect(changes[0]!.previous_cursor).toContain("99");
    },
  );
  it("revokes a subscription and subsequent HTTP access", async () => {
    const { transport } = setup("revoked");
    for await (const frame of stream()) {
      if (frame.type === "subscription.revoked") break;
    }
    await expect(
      transport.readSnapshot("campaign-1", signal()),
    ).rejects.toMatchObject({ code: "not_found" });
  });
  it("retains committed action when narration fails", async () => {
    const { transport } = setup("narration-failure");
    const action = await transport.submitAction(
      "campaign-1",
      fixtureCommand,
      signal(),
    );
    await finish(transport, action.id);
    const read = async () => {
      for await (const chunk of transport.narrate(
        "campaign-1",
        action.id,
        signal(),
      ))
        expect(chunk.status).toBe("provisional");
    };
    await expect(read()).rejects.toThrow("Narration unavailable");
    expect(
      (await transport.getAction("campaign-1", action.id, signal())).status,
    ).toBe("succeeded");
  });
});
