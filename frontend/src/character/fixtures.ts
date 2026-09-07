import { FixtureTransport, fixtureSnapshot } from "../play/fixtures";
import {
  TransportError,
  wait,
  type Action,
  type SubmitAction,
  type Snapshot,
} from "../play/transport";
import type { InventoryCommand } from "./presentation";
import { enrichSnapshot } from "./sample-data";
export const inventoryJourneys = [
  "inventory",
  "illegal-equip",
  "use-retry",
  "full-container",
  "invalid-container",
  "remote-transfer",
  "encumbrance",
  "capture",
  "recovery",
  "inventory-conflict",
  "empty",
] as const;
export type InventoryJourney = (typeof inventoryJourneys)[number];
type Request = SubmitAction | InventoryCommand;
interface ActionRecord {
  campaignId: string;
  request: Request;
  action: Action;
  polls: number;
}
const timestamp = "2026-09-06T22:00:00Z";
export function inventorySnapshot(
  campaignId: string,
  journey: InventoryJourney,
  committed = false,
  operation: Request["intent"]["kind"] = "use_item",
): Snapshot {
  const s = enrichSnapshot(fixtureSnapshot(campaignId));
  const inventory = s.inventories[0]!,
    actor = s.characters[0]!,
    details = s.inventoryDetails![actor.id]!;
  if (journey === "empty") {
    inventory.items = [];
    inventory.total_weight_grams = 0;
    inventory.encumbrance = "none";
    details.items = {};
    details.containers = [];
    actor.equipped_item_ids = [];
    s.characterDetails![actor.id]!.effects = [];
  }
  if (journey === "capture" || journey === "recovery") {
    inventory.items.forEach((item) => {
      item.location = "confiscated";
      item.container_id = null;
      item.allowed_actions = ["inspect"];
      const meta = details.items[item.id]!;
      meta.custody = "Confiscated · custodian and location unknown";
      meta.affordances.forEach((a) => {
        if (a.kind !== "inspect") {
          a.allowed = false;
          a.reason = "Not in your custody.";
        }
      });
    });
    inventory.total_weight_grams = 0;
    inventory.encumbrance = "none";
    actor.equipped_item_ids = [];
    actor.movement = [
      { id: "move", label: "Move", value: 5 },
      { id: "speed", label: "Basic Speed", value: 5.5 },
    ];
    s.characterDetails![actor.id]!.effects = [];
  }
  if (committed) {
    inventory.version = "i2";
    actor.version = "h2";
    s.scene.version = "s2";
    details.version = "i2";
    if (
      journey === "use-retry" ||
      (journey === "inventory" && operation === "use_item")
    ) {
      inventory.items[0]!.quantity = 1;
      inventory.total_weight_grams = 4250;
      actor.hp.current = 9;
    }
    if (
      journey === "encumbrance" ||
      (journey === "inventory" && operation === "drop")
    ) {
      inventory.items = inventory.items.filter((i) => i.id !== "coat-1");
      inventory.total_weight_grams = 1800;
      inventory.encumbrance = "none";
      actor.equipped_item_ids = [];
      actor.movement = [
        { id: "move", label: "Move", value: 5 },
        { id: "speed", label: "Basic Speed", value: 5.5 },
      ];
      s.characterDetails![actor.id]!.effects = [];
    }
    if (journey === "inventory" && operation === "equip") {
      inventory.items.find((i) => i.id === "sword-1")!.location = "equipped";
      actor.equipped_item_ids = ["coat-1", "sword-1"];
    }
    if (journey === "inventory" && operation === "store") {
      const item = inventory.items[0]!;
      item.quantity = 1;
      inventory.items.push({
        ...item,
        id: "bandage-stored",
        location: "stored",
        container_id: "satchel-1",
      });
      details.items["bandage-stored"] = structuredClone(
        details.items[item.id]!,
      );
      details.containers[0]!.capacityLabel = "50 / 2,000 g stored";
    }
    if (journey === "inventory" && operation === "transfer") {
      inventory.items[0]!.quantity = 1;
      inventory.total_weight_grams = 4250;
    }
    if (journey === "recovery") {
      inventory.items.forEach((item) => {
        item.location = item.id === "coat-1" ? "equipped" : "carried";
        item.allowed_actions =
          item.id === "bandage-1" ? ["inspect", "use_item"] : ["inspect"];
        const normal = enrichSnapshot(fixtureSnapshot(campaignId))
          .inventoryDetails![actor.id]!.items[item.id]!;
        details.items[item.id] = normal;
      });
      inventory.total_weight_grams = 4300;
      inventory.encumbrance = "light";
      actor.equipped_item_ids = ["coat-1"];
      actor.movement = enrichSnapshot(
        fixtureSnapshot(campaignId),
      ).characters[0]!.movement;
      s.characterDetails![actor.id]!.effects = enrichSnapshot(
        fixtureSnapshot(campaignId),
      ).characterDetails![actor.id]!.effects;
    }
  }
  if (journey === "inventory-conflict" && committed) {
    inventory.items[0]!.quantity = 1;
    inventory.total_weight_grams = 4250;
  }
  return s;
}
/** Selected script determines responses. There is no local inventory simulation. */
export class InventoryFixtureTransport extends FixtureTransport {
  readonly inventoryPreview = this;
  readonly inventoryRequests: Request[] = [];
  private reads = new Map<string, number>();
  private records = new Map<string, ActionRecord>();
  private commands = new Map<string, string>();
  private committedInventory = new Set<string>();
  private uncertain = false;
  private conflict = false;
  private operations = new Map<string, Request["intent"]["kind"]>();
  constructor(
    private inventoryJourney: InventoryJourney = "inventory",
    private delay = 100,
  ) {
    super("resolve", delay);
  }
  override async readSnapshot(campaignId: string, signal: AbortSignal) {
    await wait(this.delay, signal);
    const reads = (this.reads.get(campaignId) ?? 0) + 1;
    this.reads.set(campaignId, reads);
    if (this.inventoryJourney === "recovery" && reads > 1)
      this.recover(campaignId);
    return inventorySnapshot(
      campaignId,
      this.inventoryJourney,
      this.committedInventory.has(campaignId) || this.conflict,
      this.operations.get(campaignId),
    );
  }
  override async submitAction(
    campaignId: string,
    request: SubmitAction,
    signal: AbortSignal,
  ) {
    return this.accept(campaignId, request, signal);
  }
  async submitInventory(
    campaignId: string,
    request: InventoryCommand,
    signal: AbortSignal,
  ) {
    return this.accept(campaignId, request, signal);
  }
  private async accept(
    campaignId: string,
    request: Request,
    signal: AbortSignal,
  ) {
    this.inventoryRequests.push(structuredClone(request));
    await wait(this.delay, signal);
    const old = this.commands.get(request.command_id);
    if (old) {
      const record = this.records.get(old)!;
      if (
        record.campaignId !== campaignId ||
        JSON.stringify(record.request) !== JSON.stringify(request)
      )
        throw new TransportError(
          "idempotency_conflict",
          "The command ID was reused with different input.",
        );
      return structuredClone(record.action);
    }
    if (this.inventoryJourney === "inventory-conflict") {
      this.conflict = true;
      throw new TransportError(
        "stale_version",
        "Inventory changed before this request was accepted. Reload and review the new quantities.",
      );
    }
    const id = `inventory-action-${this.records.size + 1}`;
    const action: Action = {
      id,
      actor_id: request.actor_id,
      scene_id: request.scene_id,
      command_id: request.command_id,
      version: "a1",
      created_at: timestamp,
      updated_at: timestamp,
      status: "submitted",
    };
    this.records.set(id, {
      campaignId,
      request: structuredClone(request),
      action,
      polls: 0,
    });
    this.commands.set(request.command_id, id);
    if (this.inventoryJourney === "use-retry" && !this.uncertain) {
      this.uncertain = true;
      throw new TransportError(
        "network",
        "The acknowledgement was lost. Retry this exact request; it will not consume the item twice.",
      );
    }
    return structuredClone(action);
  }
  override async getAction(
    campaignId: string,
    id: string,
    signal: AbortSignal,
  ) {
    const record = this.records.get(id);
    if (!record) return super.getAction(campaignId, id, signal);
    await wait(this.delay, signal);
    if (record.campaignId !== campaignId)
      throw new TransportError("not_found", "Action unavailable.");
    if (["succeeded", "rejected", "cancelled"].includes(record.action.status))
      return structuredClone(record.action);
    record.polls++;
    const common = {
      id,
      actor_id: record.request.actor_id,
      scene_id: record.request.scene_id,
      command_id: record.request.command_id,
      created_at: timestamp,
      updated_at: timestamp,
    };
    if (record.polls === 1)
      record.action = { ...common, version: "a2", status: "resolving" };
    else {
      const rejection: Partial<Record<InventoryJourney, string>> = {
        "illegal-equip": "This item cannot be equipped in the selected slot.",
        "full-container": "The selected container is full.",
        "invalid-container": "The container is no longer accessible.",
        "remote-transfer": "The recipient is no longer within reach.",
      };
      const reason = rejection[this.inventoryJourney];
      if (reason)
        record.action = {
          ...common,
          version: "a3",
          status: "rejected",
          error: {
            code: "illegal_action",
            message: reason,
            retryable: false,
            request_id: record.request.command_id,
            field_errors: [],
          },
        };
      else {
        // Inspect is always a read-like action and never applies a resource projection change.
        const changes = record.request.intent.kind !== "inspect";
        if (changes) {
          this.committedInventory.add(campaignId);
          this.operations.set(campaignId, record.request.intent.kind);
        }
        record.action = {
          ...common,
          version: "a3",
          status: "succeeded",
          resolution: {
            summary: changes
              ? this.inventoryJourney === "encumbrance"
                ? "The coat is dropped. Encumbrance is now none."
                : record.request.intent.kind === "use_item"
                  ? "The item use is committed. One bandage remains."
                  : `The ${record.request.intent.kind} request is committed.`
              : "You inspect the known item description. Nothing changes.",
            checks: [],
            changed_resources: changes
              ? [
                  {
                    resource_type: "inventory",
                    resource_id: record.request.actor_id,
                    version: "i2",
                  },
                  {
                    resource_type: "character",
                    resource_id: record.request.actor_id,
                    version: "h2",
                  },
                  {
                    resource_type: "scene",
                    resource_id: record.request.scene_id,
                    version: "s2",
                  },
                ]
              : [],
            game_time: { ticks: 12, tick_duration_ms: 1000 },
          },
        };
      }
    }
    return structuredClone(record.action);
  }
  override async listActions(campaignId: string, signal: AbortSignal) {
    await wait(this.delay, signal);
    return structuredClone(
      [...this.records.values()]
        .filter((r) => r.campaignId === campaignId)
        .map((r) => r.action),
    );
  }
  override async *narrate(
    _campaignId: string,
    _actionId: string,
    signal: AbortSignal,
  ) {
    await wait(this.delay, signal);
    yield {
      text: "The authoritative result above records what changed.",
      status: "complete" as const,
    };
  }
  /** Test driver for an external capture/recovery update. UI only refetches the resulting projection. */
  recover(campaignId: string) {
    this.committedInventory.add(campaignId);
  }
}
