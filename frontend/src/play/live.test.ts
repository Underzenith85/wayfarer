import { describe, expect, it, vi } from "vitest";
import fixture from "./live-fixture.json";
import { LiveTransport } from "./live";

describe("authenticated engine adapter", () => {
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
