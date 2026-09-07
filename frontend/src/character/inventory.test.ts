import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  InventoryFixtureTransport,
  inventorySnapshot,
  inventoryJourneys,
  type InventoryJourney,
} from "./fixtures";
import { PlayStore } from "../play/store";
import { FixtureTransport } from "../play/fixtures";
import type { InventoryIntent } from "./presentation";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schemas from "../../../contracts/v1/schemas.json";
const stores: PlayStore[] = [];
async function setup(journey: InventoryJourney = "inventory") {
  const transport = new InventoryFixtureTransport(journey, 1),
    store = new PlayStore(transport, () => {}, 1);
  stores.push(store);
  await store.select("campaign-1");
  return { transport, store };
}
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
});
describe("authoritative item operations", () => {
  it("consumes once after an uncertain acknowledgement and blocks double submission", async () => {
    const { store, transport } = await setup("use-retry");
    const intent = {
      kind: "use_item",
      item_id: "bandage-1",
      quantity: 1,
    } as const;
    const first = store.sendInventory(intent);
    await store.sendInventory(intent);
    await first;
    expect(transport.inventoryRequests).toHaveLength(1);
    expect(
      store.getSnapshot().snapshot?.inventories[0]?.items[0]?.quantity,
    ).toBe(2);
    expect(store.getSnapshot().retry).not.toBeNull();
    await store.retry();
    expect(transport.inventoryRequests[1]).toEqual(
      transport.inventoryRequests[0],
    );
    expect(store.getSnapshot().snapshot?.inventories[0]).toMatchObject({
      version: "i2",
      items: [
        { id: "bandage-1", quantity: 1 },
        { id: "sword-1" },
        { id: "satchel-1" },
        { id: "coat-1" },
      ],
    });
    expect(store.getSnapshot().entries).toHaveLength(1);
  });
  it.each([
    [
      "illegal-equip",
      { kind: "equip", item_id: "sword-1", slot_id: "main-hand" },
      "cannot be equipped",
    ],
    [
      "full-container",
      {
        kind: "store",
        item_id: "bandage-1",
        quantity: 1,
        container_id: "satchel-1",
      },
      "container is full",
    ],
    [
      "invalid-container",
      {
        kind: "store",
        item_id: "bandage-1",
        quantity: 1,
        container_id: "satchel-1",
      },
      "no longer accessible",
    ],
    [
      "remote-transfer",
      {
        kind: "transfer",
        item_id: "bandage-1",
        quantity: 1,
        recipient_actor_id: "guide-1",
      },
      "no longer within reach",
    ],
  ] satisfies [InventoryJourney, InventoryIntent, string][])(
    "renders %s rejection without optimistic changes",
    async (journey, intent, message) => {
      const { store } = await setup(journey);
      const before = store.getSnapshot().snapshot;
      await store.sendInventory(intent);
      const action = store.getSnapshot().entries[0]?.action;
      expect(action?.status).toBe("rejected");
      if (action?.status === "rejected")
        expect(action.error.message).toContain(message);
      expect(store.getSnapshot().snapshot).toEqual(before);
      expect(store.getSnapshot().retry).toBeNull();
    },
  );
  it("includes all resource versions and changes encumbrance only after commit", async () => {
    const { store, transport } = await setup("encumbrance");
    const states: { status: string | undefined; weight: number | undefined }[] =
      [];
    store.subscribe(() =>
      states.push({
        status: store.getSnapshot().entries[0]?.action?.status,
        weight:
          store.getSnapshot().snapshot?.inventories[0]?.total_weight_grams,
      }),
    );
    await store.sendInventory({ kind: "drop", item_id: "coat-1", quantity: 1 });
    expect(transport.inventoryRequests[0]).toMatchObject({
      expected_versions: { scene: "s1", character: "h1", inventory: "i1" },
      intent: { kind: "drop", item_id: "coat-1", quantity: 1 },
    });
    expect(
      states
        .filter((s) => s.status === "submitted" || s.status === "resolving")
        .every((s) => s.weight === 4300),
    ).toBe(true);
    expect(store.getSnapshot().snapshot?.inventories[0]).toMatchObject({
      encumbrance: "none",
      total_weight_grams: 1800,
    });
    expect(
      store.getSnapshot().snapshot?.characters[0]?.movement[0]?.value,
    ).toBe(5);
  });
  it("requires explicit refresh and reconsideration after a version conflict", async () => {
    const { store, transport } = await setup("inventory-conflict");
    await store.sendInventory({
      kind: "use_item",
      item_id: "bandage-1",
      quantity: 1,
    });
    expect(store.getSnapshot().needsRefresh).toBe(true);
    expect(store.getSnapshot().retry).toBeNull();
    await store.sendInventory({ kind: "drop", item_id: "coat-1", quantity: 1 });
    expect(transport.inventoryRequests).toHaveLength(1);
    await store.select("campaign-1");
    expect(store.getSnapshot().needsRefresh).toBe(false);
    expect(store.getSnapshot().snapshot?.inventories[0]?.version).toBe("i2");
    expect(transport.inventoryRequests).toHaveLength(1);
  });
  it("keeps confiscated identities but no hidden custodian/location; recovery restores legal operations", async () => {
    const { store, transport } = await setup("recovery");
    const snapshot = store.getSnapshot().snapshot!;
    expect(
      snapshot.inventories[0]?.items.every(
        (i) => i.location === "confiscated" && i.container_id === null,
      ),
    ).toBe(true);
    expect(store.inventoryBlockReason("bandage-1", "use_item")).toContain(
      "custody",
    );
    await store.sendInventory({
      kind: "use_item",
      item_id: "bandage-1",
      quantity: 1,
    });
    expect(transport.inventoryRequests).toHaveLength(0);
    transport.recover("campaign-1");
    await store.select("campaign-1");
    expect(
      store.getSnapshot().snapshot?.inventories[0]?.items[0]?.location,
    ).toBe("carried");
    expect(store.inventoryBlockReason("bandage-1", "use_item")).toBeNull();
  });
  it("disallows unknown targets, inaccessible containers and invalid quantities before transport", async () => {
    const { store, transport } = await setup();
    await store.sendInventory({
      kind: "store",
      item_id: "bandage-1",
      quantity: 1,
      container_id: "sealed-chest",
    });
    await store.sendInventory({
      kind: "transfer",
      item_id: "bandage-1",
      quantity: 1,
      recipient_actor_id: "distant-ally",
    });
    await store.sendInventory({
      kind: "use_item",
      item_id: "bandage-1",
      quantity: 3,
    });
    await store.sendInventory({
      kind: "use_item",
      item_id: "bandage-1",
      quantity: 0.5,
    });
    await store.sendInventory({
      kind: "equip",
      item_id: "sword-1",
      slot_id: "unknown",
    });
    expect(transport.inventoryRequests).toEqual([]);
  });
  it("preserves unsent prose when a contextual item action commits", async () => {
    const { store } = await setup();
    store.saveDraft("action", "A plan for the locked door");
    await store.sendInventory({ kind: "inspect", target_id: "sword-1" });
    expect(store.readDraft("action")).toBe("A plan for the locked door");
    expect(store.getSnapshot().snapshot?.inventories[0]?.version).toBe("i1");
  });
  it("does not send proposed intents through a connection lacking the preview seam", async () => {
    const transport = new FixtureTransport("resolve", 1);
    const send = vi.spyOn(transport, "submitAction");
    const store = new PlayStore(transport, () => {}, 1);
    stores.push(store);
    await store.select("campaign-1");
    await store.sendInventory({
      kind: "equip",
      item_id: "sword-1",
      slot_id: "main-hand",
    });
    expect(store.inventoryBlockReason("sword-1", "equip")).toContain(
      "integrated inventory contract",
    );
    expect(send).not.toHaveBeenCalled();
  });
  it("rejects mutation for a spectator even when fixtures include details", async () => {
    const { store, transport } = await setup();
    vi.spyOn(transport, "readSnapshot").mockImplementation(async () => {
      const s = inventorySnapshot("campaign-1", "inventory");
      s.campaign.membership.role = "spectator";
      return s;
    });
    await store.select("campaign-1");
    await store.sendInventory({
      kind: "use_item",
      item_id: "bandage-1",
      quantity: 1,
    });
    expect(transport.inventoryRequests).toEqual([]);
  });
});
describe("inventory fixture contracts", () => {
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  addFormats(ajv);
  ajv.addSchema(schemas);
  const check = (name: string, value: unknown) => {
    const validate = ajv.getSchema(`${schemas.$id}#/$defs/${name}`)!;
    expect(validate(value), JSON.stringify(validate.errors)).toBe(true);
  };
  it("keeps frozen projections schema-valid for every journey and response version", () => {
    for (const journey of inventoryJourneys)
      for (const committed of [false, true]) {
        const s = inventorySnapshot("campaign-1", journey, committed);
        check("Character", s.characters[0]);
        check("Inventory", s.inventories[0]);
        check("Scene", s.scene);
      }
  });
  it("validates use requests and committed/rejected Action unions", async () => {
    for (const journey of ["use-retry", "illegal-equip"] as const) {
      const { store, transport } = await setup(journey);
      await store.sendInventory(
        journey === "use-retry"
          ? { kind: "use_item", item_id: "bandage-1", quantity: 1 }
          : { kind: "equip", item_id: "sword-1", slot_id: "main-hand" },
      );
      if (store.getSnapshot().retry) await store.retry();
      check("Action", store.getSnapshot().entries[0]?.action);
      if (journey === "use-retry")
        check("SubmitAction", transport.inventoryRequests[0]);
      else {
        const validate = ajv.getSchema(`${schemas.$id}#/$defs/SubmitAction`)!;
        expect(validate(transport.inventoryRequests[0])).toBe(false);
      }
    }
  });
});
