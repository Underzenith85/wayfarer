import { describe, it, expect, vi, afterEach } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schemas from "../../../contracts/v1/schemas.json";
import {
  MultiplayerAuthority,
  type Identity,
} from "../../fixtures/multiplayer-authority";
import { PlayStore } from "../play/store";
import {
  TransportError,
  type PlayTransport,
  type SubmitAction,
} from "../play/transport";
import {
  scopeKey,
  type MultiplayerPort,
  type MultiplayerView,
  type ScopeEvent,
  type TableCommand,
} from "./model";

const stores: PlayStore[] = [];
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
  localStorage.clear();
});
function harness(
  authority = new MultiplayerAuthority("unit"),
  identity: Identity = "rescuer",
) {
  let actorId: string | null = null;
  const callbacks: ((event: ScopeEvent) => void)[] = [];
  const port: MultiplayerPort = {
    read: vi.fn(async (_campaign, actor) => {
      actorId = actor;
      return authority.read(identity, actor);
    }),
    watch: (_view, _signal, receive) => {
      callbacks.push(receive);
    },
    command: vi.fn(async (command) => {
      authority.command(identity, command);
    }),
  };
  const transport: PlayTransport = {
    principalId: authority.principal(identity),
    sample: true,
    multiplayer: port,
    listCampaigns: async () => [
      authority.read(identity, null).snapshot.campaign,
    ],
    readSnapshot: async () => authority.read(identity, actorId).snapshot,
    listActions: async () => authority.read(identity, actorId).actions,
    submitAction: vi.fn(async (_campaign, command) =>
      authority.submit(identity, command),
    ),
    clarifyAction: async (_campaign, id, command) =>
      authority.clarify(identity, actorId ?? "hero-1", id, command),
    getAction: async (_campaign, id) =>
      authority.read(identity, actorId).actions.find((a) => a.id === id)!,
    async *narrate() {
      yield { text: "Recorded result.", status: "complete" };
    },
  };
  const clear = vi.fn(),
    store = new PlayStore(transport, clear, 1);
  stores.push(store);
  return {
    authority,
    identity,
    port,
    transport,
    store,
    clear,
    emit: (event: ScopeEvent) => callbacks.at(-1)!(event),
    callbacks,
  };
}
function command(view: MultiplayerView): SubmitAction {
  return {
    command_id: crypto.randomUUID(),
    actor_id: view.scope.actorId,
    scene_id: view.scope.sceneId,
    expected_versions: {
      scene: view.snapshot.scene.version,
      character: view.snapshot.characters[0]!.version,
    },
    intent: { kind: "text", text: "Look around." },
  };
}
describe("multiplayer authority and scoped recovery", () => {
  it("fences late private reads after session revocation", async () => {
    const h = harness();
    let finish!: (view: MultiplayerView) => void;
    vi.mocked(h.port.read).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const selecting = h.store.select("campaign-1");
    h.store.expire();
    finish(h.authority.read("rescuer", null));
    await selecting;
    expect(h.store.getSnapshot()).toMatchObject({
      expired: true,
      snapshot: null,
      multiplayer: null,
      entries: [],
    });
  });
  it("delivers private payloads and rejects another player's actor without client filtering", () => {
    const a = new MultiplayerAuthority("privacy"),
      captive = a.read("captive", null),
      rescuer = a.read("rescuer", null);
    expect(JSON.stringify(captive)).not.toContain("alder");
    expect(JSON.stringify(rescuer)).not.toContain("copper finch");
    expect(JSON.stringify(rescuer)).not.toContain("locked cell");
    expect(() => a.read("rescuer", "hero-1")).toThrow("access changed");
    expect(() => a.submit("rescuer", command(captive))).toThrow(
      "access changed",
    );
  });
  it("keeps captive decisions independent from rescuers and other controlled scenes", async () => {
    const a = new MultiplayerAuthority("independent"),
      c = harness(a, "captive"),
      r = harness(a);
    await c.store.select("campaign-1");
    await r.store.select("campaign-1");
    await c.store.send("action", "Examine the lock");
    expect(c.store.getSnapshot().entries[0]!.action?.status).toBe(
      "needs_clarification",
    );
    expect(r.store.canSend("text")).toBe(true);
    await c.store.clarify(c.store.getSnapshot().entries[0]!.id, {
      choice_id: "wait",
    });
    expect(c.store.getSnapshot().entries[0]!.action?.status).toBe("succeeded");
    await r.store.send("action", "Follow the path");
    expect(r.store.getSnapshot().entries[0]!.action?.status).toBe("succeeded");
    r.store.saveDraft("action", "Ivo's draft");
    r.store.chooseActor("hero-3");
    await vi.waitFor(() =>
      expect(r.store.getSnapshot().actorId).toBe("hero-3"),
    );
    expect(r.store.getSnapshot().drafts.action.text).toBe("");
    r.store.chooseActor("hero-2");
    await vi.waitFor(() =>
      expect(r.store.getSnapshot().drafts.action.text).toBe("Ivo's draft"),
    );
  });
  it("pauses every authoritative action offline and restores the scoped draft after missed changes", async () => {
    const h = harness();
    await h.store.select("campaign-1");
    h.store.saveDraft("action", "Keep searching");
    const checkpoint = h.store.getSnapshot().multiplayer!.checkpoint.cursor;
    h.store.disconnect();
    expect(h.store.canSend("text")).toBe(false);
    await h.store.send("action", "Must not send");
    await h.store.tableCommand({ kind: "ready", ready: true });
    expect(h.transport.submitAction).not.toHaveBeenCalled();
    expect(h.port.command).not.toHaveBeenCalled();
    expect(h.store.inventoryBlockReason("bandage-1", "use_item")).toContain(
      "Reconnect",
    );
    h.authority.scenario("missed", "rescuer");
    await h.store.reconnect();
    expect(h.store.getSnapshot().drafts.action.text).toBe("Keep searching");
    expect(h.store.getSnapshot().multiplayer!.checkpoint.cursor).not.toBe(
      checkpoint,
    );
    expect(h.store.canSend("text")).toBe(true);
  });
  it("converges duplicate, reordered and gapped invalidations without applying stale data", async () => {
    const h = harness();
    await h.store.select("campaign-1");
    const first = h.store.getSnapshot().multiplayer!;
    const oldCallback = h.callbacks[0]!;
    h.emit({
      kind: "changed",
      scope: first.scope,
      checkpoint: first.checkpoint,
      previousCursor: "duplicate",
    });
    expect(h.port.read).toHaveBeenCalledTimes(1);
    h.authority.scenario("missed", "rescuer");
    h.authority.scenario("missed", "rescuer");
    const current = h.authority.read("rescuer", null);
    h.emit({
      kind: "changed",
      scope: first.scope,
      checkpoint: current.checkpoint,
      previousCursor: "unseen-gap",
    });
    await vi.waitFor(() =>
      expect(h.store.getSnapshot().multiplayer?.checkpoint).toEqual(
        current.checkpoint,
      ),
    );
    oldCallback({ kind: "revoked" });
    expect(h.store.getSnapshot().expired).toBe(false);
    h.emit({
      kind: "changed",
      scope: first.scope,
      checkpoint: first.checkpoint,
      previousCursor: "reordered",
    });
    await vi.waitFor(() =>
      expect(h.store.getSnapshot().multiplayer?.checkpoint).toEqual(
        current.checkpoint,
      ),
    );
    expect(h.clear).toHaveBeenCalled();
  });
  it("ignores wrong-scope events and clears every private surface on revocation", async () => {
    const h = harness();
    await h.store.select("campaign-1");
    h.store.saveDraft("action", "Private plan");
    const view = h.store.getSnapshot().multiplayer!;
    h.emit({
      kind: "changed",
      scope: { ...view.scope, actorId: "hero-1" },
      checkpoint: { cursor: "foreign", epoch: "foreign" },
      previousCursor: view.checkpoint.cursor,
    });
    expect(h.port.read).toHaveBeenCalledTimes(1);
    h.emit({ kind: "revoked" });
    expect(h.store.getSnapshot()).toMatchObject({
      expired: true,
      snapshot: null,
      multiplayer: null,
      entries: [],
      actorId: null,
      retry: null,
      tableRetry: null,
    });
    expect(
      Object.keys(localStorage).filter((k) => k.startsWith("wayfarer:draft:")),
    ).toEqual([]);
  });
  it("control reassignment during disconnect purges stale ownership and drafts", async () => {
    const h = harness();
    await h.store.select("campaign-1");
    h.store.saveDraft("action", "Old controller secret");
    h.store.disconnect();
    h.authority.scenario("reassign", "rescuer");
    await h.store.reconnect();
    expect(h.store.getSnapshot().expired).toBe(true);
    expect(h.store.getSnapshot().drafts.action.text).toBe("");
  });
  it("recovers an accepted request after lost acknowledgement without submitting again", async () => {
    const h = harness();
    await h.store.select("campaign-1");
    vi.mocked(h.transport.submitAction).mockImplementationOnce(
      async (_id, request) => {
        h.authority.submit("rescuer", request);
        throw new TransportError("network", "Lost acknowledgement");
      },
    );
    await h.store.send("action", "Look");
    expect(h.store.getSnapshot().connection).toBe("offline");
    await h.store.reconnect();
    expect(h.store.getSnapshot().retry).toBeNull();
    expect(h.store.getSnapshot().entries).toHaveLength(1);
    expect(h.transport.submitAction).toHaveBeenCalledTimes(1);
  });
  it("retains the exact unaccepted request across reconnect and requires explicit retry", async () => {
    const h = harness();
    await h.store.select("campaign-1");
    vi.mocked(h.transport.submitAction).mockRejectedValueOnce(
      new TransportError("network", "Not delivered"),
    );
    await h.store.send("action", "Look");
    const request = vi.mocked(h.transport.submitAction).mock.calls[0]![1];
    await h.store.reconnect();
    expect(h.transport.submitAction).toHaveBeenCalledTimes(1);
    await h.store.retry();
    expect(vi.mocked(h.transport.submitAction).mock.calls[1]![1]).toEqual(
      request,
    );
    expect(h.authority.read("rescuer", null).actions).toHaveLength(1);
  });
  it("rejects stale and remote group commands and retries table receipts exactly once", async () => {
    const h = harness();
    await h.store.select("campaign-1");
    const view = h.store.getSnapshot().multiplayer!;
    const request: TableCommand = {
      commandId: crypto.randomUUID(),
      scope: view.scope,
      expectedGroupVersion: view.groupVersion,
      expectedMembershipVersion: view.snapshot.campaign.membership.version,
      intent: { kind: "rejoin", destinationId: "cell" },
    };
    expect(() => h.authority.command("rescuer", request)).toThrow(
      "not reachable",
    );
    h.authority.scenario("missed", "rescuer");
    expect(() =>
      h.authority.command("rescuer", {
        ...request,
        intent: { kind: "ready", ready: true },
      }),
    ).toThrow("changed");
    await h.store.reconnect();
    vi.mocked(h.port.command).mockImplementationOnce(async (c) => {
      h.authority.command("rescuer", c);
      throw new TransportError("network", "Unknown acknowledgement");
    });
    await h.store.tableCommand({ kind: "ooc", text: "Ready when you are" });
    await h.store.reconnect();
    await h.store.retryTable();
    expect(h.authority.read("rescuer", null).ooc.messages).toHaveLength(1);
    expect(vi.mocked(h.port.command).mock.calls[0]![0]).toEqual(
      vi.mocked(h.port.command).mock.calls[1]![0],
    );
  });
  it("reunion shares presence but retains individual knowledge; OOC does not create an action", () => {
    const a = new MultiplayerAuthority("reunion");
    a.scenario("rescue", "rescuer");
    for (const who of ["captive", "rescuer"] as const) {
      const v = a.read(who, null);
      a.command(who, {
        commandId: crypto.randomUUID(),
        scope: v.scope,
        expectedGroupVersion: v.groupVersion,
        expectedMembershipVersion: v.snapshot.campaign.membership.version,
        intent: { kind: "rejoin", destinationId: "reunion" },
      });
    }
    const c = a.read("captive", null),
      r = a.read("rescuer", null);
    expect(c.presence).toHaveLength(2);
    expect(r.presence).toHaveLength(2);
    expect(JSON.stringify(r)).not.toContain("copper finch");
    expect(JSON.stringify(c)).not.toContain("alder");
    a.command("rescuer", {
      commandId: crypto.randomUUID(),
      scope: r.scope,
      expectedGroupVersion: r.groupVersion,
      expectedMembershipVersion: r.snapshot.campaign.membership.version,
      intent: { kind: "ooc", text: "Hello table" },
    });
    expect(a.read("captive", null).ooc.messages[0]?.text).toBe("Hello table");
    expect(a.read("rescuer", null).actions).toEqual([]);
  });
  it("validates delivered frozen resources and isolates cache identities", () => {
    const ajv = new Ajv2020({ strict: false, allErrors: true });
    addFormats(ajv);
    ajv.addSchema(schemas);
    const a = new MultiplayerAuthority("schemas");
    for (const who of ["captive", "rescuer"] as const) {
      let v = a.read(who, null);
      a.submit(who, command(v));
      v = a.read(who, null);
      const objects: { name: string; value: unknown }[] = [
        { name: "Campaign", value: v.snapshot.campaign },
        { name: "Scene", value: v.snapshot.scene },
        { name: "Session", value: v.snapshot.session },
        ...v.snapshot.characters.map((value) => ({ name: "Character", value })),
        ...v.snapshot.inventories.map((value) => ({
          name: "Inventory",
          value,
        })),
        ...v.actions.map((value) => ({ name: "Action", value })),
      ];
      for (const { name, value } of objects) {
        const validate = ajv.getSchema(`${schemas.$id}#/$defs/${name}`)!;
        expect(validate(value), JSON.stringify(validate.errors)).toBe(true);
      }
      expect(
        scopeKey(a.principal(who), v.scope, v.checkpoint.epoch),
      ).not.toEqual(scopeKey("other", v.scope, v.checkpoint.epoch));
      expect(
        scopeKey(a.principal(who), v.scope, v.checkpoint.epoch),
      ).not.toEqual(scopeKey(a.principal(who), v.scope, "another-grant"));
    }
  });
});
