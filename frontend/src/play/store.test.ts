import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlayStore } from "./store";
import { FixtureTransport, fixtureSnapshot, type Journey } from "./fixtures";
import type { Snapshot } from "./transport";
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
    expect(store.readDraft("action")).toBe("Use bandage");
    await store.retry();
    expect(transport.requests[1]).toEqual(transport.requests[0]);
    expect(store.getSnapshot().entries).toHaveLength(1);
    expect(store.getSnapshot().drafts.action).toBe("");
    expect(store.getSnapshot().entries[0]?.action?.status).toBe("succeeded");
  });
  it("does not treat business rejection as a retry or apply resource changes", async () => {
    const { store } = await start("reject");
    await store.send("action", "Use the item");
    expect(store.getSnapshot().entries[0]?.action?.status).toBe("rejected");
    expect(store.getSnapshot().retry).toBeNull();
    expect(store.getSnapshot().snapshot?.characters[0]?.version).toBe("h1");
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
    expect(store.getSnapshot().drafts.action).toBe("");
    store.saveDraft("action", "Harbor draft");
    await store.select("campaign-1");
    expect(store.getSnapshot().drafts).toMatchObject({
      action: "Private cellar draft",
      ooc: "A rules question",
      dialogue: "",
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
    store.saveDraft("action", "Private");
    await store.send("action", "Try action");
    expect(store.getSnapshot()).toMatchObject({
      expired: true,
      snapshot: null,
      entries: [],
      campaigns: [],
      retry: null,
      drafts: { action: "", dialogue: "", ooc: "" },
    });
    expect(
      Object.keys(localStorage).filter((k) => k.startsWith("wayfarer:draft")),
    ).toEqual([]);
    expect(clear).toHaveBeenCalledTimes(2);
  });
  it("requires reconsideration on stale versions and retains the unsent draft", async () => {
    const { store } = await start("stale");
    store.saveDraft("action", "Wait here");
    await store.send("action", "Wait here");
    expect(store.getSnapshot().retry).toBeNull();
    expect(store.readDraft("action")).toBe("Wait here");
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
  });
});
