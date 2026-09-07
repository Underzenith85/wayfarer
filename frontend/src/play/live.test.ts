import { describe, expect, it, vi } from "vitest";
import fixture from "./live-fixture.json";
import { LiveTransport } from "./live";

describe("authenticated engine adapter", () => {
  it.each([false, true])(
    "submits provider-free choices with shared time %s",
    async (shared_time) => {
      const fetcher = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({ ...fixture, shared_time, revision: 7 }),
          ),
        )
        .mockResolvedValueOnce(new Response("{}"));
      const transport = new LiveTransport(
        "alice",
        fixture.campaign_id,
        "token",
      );
      await transport.command(
        "a",
        { kind: "wait", ticks: 1 },
        new AbortController().signal,
      );
      expect(fetcher.mock.calls[1]?.[0]).toBe(
        `/campaigns/${fixture.campaign_id}/commands`,
      );
      const body = JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body));
      expect(body.actor_id).toBe("a");
      expect(body.expected_revision).toBe(7);
      const action = shared_time ? JSON.parse(body.activity_json) : body;
      expect(action).toMatchObject({
        kind: "wait",
        ticks: 1,
        actor_id: "a",
        expected_revision: 7,
      });
      if (shared_time) expect(body.kind).toBe("queue_activity");
    },
  );
  it("uses bearer identity and refuses a response for another player", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify(fixture), { status: 200 }),
      );
    const transport = new LiveTransport(
      "alice",
      fixture.campaign_id,
      "private-token",
    );
    const snapshot = await transport.readSnapshot(
      fixture.campaign_id,
      new AbortController().signal,
    );
    expect(snapshot.characters.map((c) => c.id)).toEqual(["a"]);
    expect(fetcher.mock.calls[0]?.[1]?.headers).toEqual({
      Authorization: "Bearer private-token",
    });
    fetcher.mockResolvedValue(
      new Response(JSON.stringify({ ...fixture, principal_id: "bob" }), {
        status: 200,
      }),
    );
    await expect(
      transport.readSnapshot(fixture.campaign_id, new AbortController().signal),
    ).rejects.toThrow("identity changed");
  });
  it("keeps the latest committed revision when responses arrive out of order", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...fixture, revision: 4 })),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...fixture, revision: 2 })),
      );
    const transport = new LiveTransport("alice", fixture.campaign_id, "token");
    expect(
      (
        await transport.readSnapshot(
          fixture.campaign_id,
          new AbortController().signal,
        )
      ).campaign.version,
    ).toBe("4");
    expect(
      (
        await transport.readSnapshot(
          fixture.campaign_id,
          new AbortController().signal,
        )
      ).campaign.version,
    ).toBe("4");
  });
  it("recovers a persisted pending turn using its original identity", async () => {
    const turn = {
      id: "turn-1",
      actor_id: "a",
      text: "wait",
      phase: "resolution",
      committed: false,
      narration: "",
      narration_available: false,
    };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...fixture, director: [turn] })),
      )
      .mockResolvedValueOnce(new Response("{}"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...fixture,
            revision: 5,
            director: [{ ...turn, phase: "complete", committed: true }],
          }),
        ),
      );
    const transport = new LiveTransport("alice", fixture.campaign_id, "token");
    expect(
      (
        await transport.getAction(
          fixture.campaign_id,
          "turn-1",
          new AbortController().signal,
        )
      ).status,
    ).toBe("succeeded");
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({
      actor_id: "a",
      command_id: "turn-1",
      text: "wait",
    });
  });
});

it("keeps a scheduled turn waiting until its durable activity resolves", async () => {
  const turn = {
    id: "scheduled",
    actor_id: "a",
    text: "wait",
    phase: "waiting",
    committed: false,
    narration: "Waiting for shared-time coordination.",
    narration_available: false,
  };
  const waiting = { ...fixture, director: [turn] };
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify(waiting)))
    .mockResolvedValueOnce(new Response("{}"))
    .mockResolvedValueOnce(new Response(JSON.stringify(waiting)));
  const transport = new LiveTransport("alice", fixture.campaign_id, "token");
  const result = await transport.getAction(
    fixture.campaign_id,
    "scheduled",
    new AbortController().signal,
  );
  expect(result.status).toBe("resolving");
  expect(result.waitingForSharedTime).toBe(true);
  expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({
    actor_id: "a",
    command_id: "scheduled",
    text: "wait",
  });
});
