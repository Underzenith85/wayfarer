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
  it("submits only the opaque server recovery choice", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...fixture, shared_time: true, revision: 9 }),
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
      { kind: "gurps_recovery", choice_id: "gurps:rest:a:a" },
      new AbortController().signal,
    );
    const body = JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body));
    expect(body).toMatchObject({
      kind: "gurps_recovery",
      choice_id: "gurps:rest:a:a",
      actor_id: "a",
      expected_revision: 9,
    });
    expect(body).not.toHaveProperty("activity_json");
    expect(body).not.toHaveProperty("target_actor_id");
    expect(body).not.toHaveProperty("skill");
    expect(body).not.toHaveProperty("technology_level");
    expect(body).not.toHaveProperty("healing");
  });
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