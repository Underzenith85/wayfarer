import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlayStore } from "./store";
import { FixtureTransport, fixtureSnapshot, type Journey } from "./fixtures";
import type { Action, Snapshot } from "./transport";
import { providerReason } from "../presentation/availability";
const stores: PlayStore[] = [];
function make(journey: Journey = "resolve") {
  const transport = new FixtureTransport(journey, 1);
  const clear = vi.fn();
  const store = new PlayStore(transport, clear, 1);
  stores.push(store);
  return { store, transport, clear };
}
async function start(journey: Journey = "resolve") {
  const result = make(journey);
  await result.store.loadCampaigns();
  await result.store.select("campaign-1");
  return result;
}
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
});
describe("scoped play journeys", () => {
  it("narrates a committed journey from the scene it arrives in (#294)", async () => {
    const { store, transport } = await start();
    const destination = structuredClone(store.getSnapshot().snapshot!);
    destination.scene.id = "destination";
    vi.spyOn(transport, "readSnapshot").mockResolvedValue(destination);
    const narration = vi.spyOn(transport, "narrate");
    await store.send("action", "Travel onward");
    expect(store.getSnapshot().snapshot?.scene.id).toBe("destination");
    expect(store.getSnapshot().entries[0]?.action?.status).toBe("succeeded");
    // The turn that moved the party is the one most worth reading, and it used
    // to be the only kind of turn that produced no prose at all.
    expect(narration).toHaveBeenCalled();
    expect(store.getSnapshot().entries[0]?.narration).toMatchObject({
      status: "complete",
    });
    expect(store.getSnapshot().busy).toBe(false);
    expect(store.getSnapshot().expired).toBe(false);
  });
  it("finishes a committed turn whose narration never arrives", async () => {
    const { store, transport } = await start();
    // A stream that stays open and says nothing must not hold the turn open.
    vi.spyOn(transport, "narrate").mockImplementation(
      // eslint-disable-next-line require-yield
      async function* (_c, _a, signal: AbortSignal) {
        await new Promise((_, reject) =>
          signal.addEventListener("abort", () => reject(signal.reason), {
            once: true,
          }),
        );
      },
    );
    store.narrationMs = 5;
    await store.send("action", "Dress the wound");
    expect(store.getSnapshot().entries[0]).toMatchObject({
      action: { status: "succeeded" },
      narration: { status: "failed" },
    });
    expect(store.getSnapshot().busy).toBe(false);
  });
  it("restores the game master's prose for a turn read back after a reload (#294)", async () => {
    const { store } = await start();
    await store.send("action", "Dress the wound");
    const told = store.getSnapshot().entries[0]?.narration?.text;
    expect(told).toContain("clean linen");
    await store.select("campaign-1");
    expect(store.getSnapshot().entries[0]?.narration).toEqual({
      text: told,
      status: "complete",
    });
  });
  it("orders the transcript by service time, not by the order replies arrived", async () => {
    const { store, transport } = await start();
    // The service is the authority on when a turn happened, and it does not
    // have to agree with the order this device saw the replies in.
    const recorded = new Map<string, string>();
    let next = "2026-09-06T22:00:02Z";
    const getAction = transport.getAction.bind(transport);
    const listActions = transport.listActions.bind(transport);
    const stamp = (a: Action) => ({
      ...a,
      created_at: recorded.get(a.id) ?? a.created_at,
    });
    vi.spyOn(transport, "getAction").mockImplementation(
      async (cid, id, signal) => {
        recorded.set(id, recorded.get(id) ?? next);
        return stamp(await getAction(cid, id, signal));
      },
    );
    vi.spyOn(transport, "listActions").mockImplementation(async (cid, signal) =>
      (await listActions(cid, signal)).map(stamp),
    );
    await store.send("action", "First taken, later recorded");
    next = "2026-09-06T22:00:01Z";
    await store.send("action", "Second taken, earlier recorded");
    expect(store.getSnapshot().entries.map((e) => e.text)).toEqual([
      "Second taken, earlier recorded",
      "First taken, later recorded",
    ]);
    // And the same order comes back from the service, not from arrival order.
    await store.select("campaign-1");
    expect(
      store.getSnapshot().entries.map((e) => e.action?.created_at),
    ).toEqual(["2026-09-06T22:00:01Z", "2026-09-06T22:00:02Z"]);
  });
  it("updates summaries only after committed results and preserves provisional narration separation", async () => {
    const { store } = await start();
    const versions: string[] = [];
    const stages: string[] = [];
    store.subscribe(() => {
      const state = store.getSnapshot();
      versions.push(state.snapshot?.characters[0]?.version ?? "");
      const a = state.entries[0];
      stages.push(`${a?.action?.status}:${a?.narration?.status}`);
    });
    await store.send("action", "Dress the wound");
    expect(versions[0]).toBe("h1");
    expect(stages).toContain("resolving:undefined");
    expect(stages).toContain("succeeded:provisional");
    expect(store.getSnapshot().snapshot?.characters[0]?.hp.current).toBe(9);
    expect(
      store.getSnapshot().snapshot?.inventories[0]?.items[0]?.quantity,
    ).toBe(1);
    expect(store.getSnapshot().entries[0]?.action?.status).toBe("succeeded");
  });
  it("continues clarification on the original action with a fresh command ID and current versions", async () => {
    const { store, transport } = await start("clarify");
    await store.send("action", "Look closer");
    const first = store.getSnapshot().entries[0]!;
    expect(first.action?.status).toBe("needs_clarification");
    expect(store.canSend("text")).toBe(false);
    await store.clarify(first.id, { choice_id: "door-1" });
    const second = transport.requests[1]!;
    expect(second.actionId).toBe(first.action?.id);
    expect(second.request.command_id).not.toBe(
      transport.requests[0]!.request.command_id,
    );
    expect(second.request).toMatchObject({
      expected_action_version: "a_needs_clarification",
      clarification_id: "clarification-1",
      expected_versions: { scene: "s1", character: "h1", inventory: "i1" },
    });
    expect(store.getSnapshot().entries).toHaveLength(1);
    expect(store.getSnapshot().entries[0]?.action?.id).toBe(first.action?.id);
  });
  it("retries unknown acceptance with identical ID and payload and clears accepted drafts", async () => {
    const { store, transport } = await start("retry");
    store.saveDraft("action", "Use bandage");
    await store.send("action", "Use bandage");
    expect(store.getSnapshot().retry).not.toBeNull();
    expect(store.readDraft("action").text).toBe("Use bandage");
    await store.retry();
    expect(transport.requests[1]).toEqual(transport.requests[0]);
    expect(store.getSnapshot().entries).toHaveLength(1);
    expect(store.getSnapshot().drafts.action.text).toBe("");
    expect(store.getSnapshot().entries[0]?.action?.status).toBe("succeeded");
  });
  it("does not treat business rejection as a retry or apply resource changes", async () => {
    const { store } = await start("reject");
    await store.send("action", "Use the item");
    // A refused attempt is a failed attempt, not a turn of the story (#298).
    expect(store.getSnapshot().entries).toEqual([]);
    expect(store.getSnapshot().attempts[0]).toMatchObject({
      text: "Use the item",
      action: { status: "rejected" },
    });
    expect(store.getSnapshot().retry).toBeNull();
    expect(store.getSnapshot().snapshot?.characters[0]?.version).toBe("h1");
  });
  it("keeps a refused attempt out of the log across a reload, and retries it (#298)", async () => {
    const { store, transport } = await start("reject");
    await store.send("action", "Use the item");
    await store.select("campaign-1");
    expect(store.getSnapshot().entries).toEqual([]);
    expect(store.getSnapshot().attempts).toHaveLength(1);
    const id = store.getSnapshot().attempts[0]!.id;
    store.dismissAttempt(id);
    expect(store.getSnapshot().attempts).toEqual([]);
    // Retrying resends the same words as a new command, never the same one.
    await store.send("action", "Use the item");
    await store.retryAttempt(store.getSnapshot().attempts[0]!.id);
    expect(transport.requests).toHaveLength(3);
    expect(
      new Set(transport.requests.map((r) => r.request.command_id)).size,
    ).toBe(3);
  });
  it("keeps the successful action when narration fails", async () => {
    const { store } = await start("narration-failure");
    await store.send("action", "Use bandage");
    expect(store.getSnapshot().entries[0]).toMatchObject({
      action: { status: "succeeded" },
      narration: { status: "failed" },
    });
    expect(store.getSnapshot().snapshot?.inventories[0]?.version).toBe("i2");
    expect(store.getSnapshot().retry).toBeNull();
  });
  it("isolates drafts by campaign, actor and channel and restores on return", async () => {
    const { store, clear } = await start();
    store.saveDraft("action", "Private cellar draft");
    store.saveDraft("ooc", "A rules question");
    await store.select("campaign-2");
    expect(store.getSnapshot().drafts.action.text).toBe("");
    store.saveDraft("action", "Harbor draft");
    await store.select("campaign-1");
    expect(store.getSnapshot().drafts).toMatchObject({
      action: { text: "Private cellar draft" },
      ooc: { text: "A rules question" },
      dialogue: { text: "" },
    });
    expect(clear).toHaveBeenCalledTimes(3);
    expect(store.resumeId()).toBe("campaign-1");
  });
  it("fences late responses after switching campaigns, even when an adapter ignores cancellation", async () => {
    const { store, transport } = await start();
    let resolve!: (s: Snapshot) => void;
    const original = transport.readSnapshot.bind(transport);
    vi.spyOn(transport, "readSnapshot")
      .mockImplementationOnce(
        () =>
          new Promise((r) => {
            resolve = r;
          }),
      )
      .mockImplementationOnce(original);
    const old = store.select("campaign-1");
    await store.select("campaign-2");
    resolve(fixtureSnapshot("campaign-1"));
    await old;
    expect(store.getSnapshot().snapshot?.campaign.id).toBe("campaign-2");
    expect(store.getSnapshot().entries).toEqual([]);
  });
  it("recovers pending choices when resuming the same campaign", async () => {
    const { store } = await start("clarify");
    await store.send("action", "Look closer");
    const id = store.getSnapshot().entries[0]?.action?.id;
    await store.select("campaign-2");
    await store.select("campaign-1");
    expect(store.getSnapshot().entries[0]?.action).toMatchObject({
      id,
      status: "needs_clarification",
    });
  });
  it("clears scoped caches and all private drafts on session expiry", async () => {
    const { store, clear } = await start("expired");
    sessionStorage.setItem(
      "wayfarer:session",
      JSON.stringify({
        credential: "secret",
        principalId: "player-1",
        campaignId: "campaign-1",
      }),
    );
    store.saveDraft("action", "Private");
    await store.send("action", "Try action");
    expect(store.getSnapshot()).toMatchObject({
      expired: true,
      snapshot: null,
      entries: [],
      campaigns: [],
      retry: null,
      drafts: {
        action: { text: "" },
        dialogue: { text: "" },
        ooc: { text: "" },
      },
    });
    expect(
      Object.keys(localStorage).filter((k) => k.startsWith("wayfarer:draft")),
    ).toEqual([]);
    // The remembered credential must not outlive the session it belongs to.
    expect(sessionStorage.getItem("wayfarer:session")).toBeNull();
    expect(clear).toHaveBeenCalledTimes(2);
  });
  it("requires reconsideration on stale versions and retains the unsent draft", async () => {
    const { store } = await start("stale");
    store.saveDraft("action", "Wait here");
    await store.send("action", "Wait here");
    expect(store.getSnapshot().retry).toBeNull();
    expect(store.readDraft("action").text).toBe("Wait here");
    expect(store.getSnapshot().error).toContain("reconsider");
  });
  it("uses typed question and text intents while retaining separate transcript channels", async () => {
    const { store, transport } = await start();
    await store.send("ooc", "What is visible?");
    await store.send("dialogue", "Who is there?");
    expect(transport.requests[0]?.request).toMatchObject({
      intent: { kind: "question", text: "What is visible?" },
    });
    expect(transport.requests[1]?.request).toMatchObject({
      intent: { kind: "text", text: "My character says: Who is there?" },
    });
    expect(store.getSnapshot().entries.map((e) => e.channel)).toEqual([
      "ooc",
      "dialogue",
    ]);
  });
  it("does not show action permission for spectators or absent capabilities", async () => {
    const { store, transport } = make();
    vi.spyOn(transport, "readSnapshot").mockImplementation(async () => {
      const s = fixtureSnapshot("campaign-1");
      s.campaign.membership.role = "spectator";
      return s;
    });
    await store.select("campaign-1");
    await store.send("action", "Try action");
    expect(transport.requests).toEqual([]);
    expect(store.canSend("text")).toBe(false);
    expect(store.sendBlockReason("text")).toBe(
      "Only a player with a controlled character can act at this table.",
    );
  });
  it("names the one condition that blocks a send, never a stale one", async () => {
    const { store, transport } = make();
    vi.spyOn(transport, "readSnapshot").mockImplementation(async () => {
      const s = fixtureSnapshot("campaign-1");
      s.campaign.capabilities = ["actions.inspect"];
      return s;
    });
    await store.select("campaign-1");
    // A missing provider is the only thing free text still waits on.
    expect(store.sendBlockReason("text")).toBe(providerReason.text);
    expect(store.sendBlockReason("question")).toBe(providerReason.question);
    expect(store.sendBlockReason("inspect")).toBeNull();
    expect(store.canSend("inspect")).toBe(true);
    store.expire();
    expect(store.sendBlockReason("inspect")).toBe("Your session has ended.");
  });
});
