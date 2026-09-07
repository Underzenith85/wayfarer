import type { Action, SubmitAction } from "../play/transport";
/** Proposed presentation surfaces, not additions to the frozen v1 wire schemas. */
export interface CharacterDetails {
  rulesLabel: string;
  effects: { id: string; label: string; description: string; source: string }[];
  points: {
    version: string;
    earned: number;
    spent: number;
    available: number;
    buildTotal: number;
    ledger: { id: string; amount: number; reason: string }[];
  };
}
export type InventoryOperation =
  "inspect" | "use_item" | "equip" | "drop" | "transfer" | "store";
export interface Affordance {
  kind: InventoryOperation;
  allowed: boolean;
  reason: string | null;
}
export interface ItemDetails {
  owner: string;
  custody: string;
  affordances: Affordance[];
}
export interface InventoryDetails {
  version: string;
  currency: { label: string; minorAmount: number; fractionDigits: number }[];
  containers: {
    id: string;
    label: string;
    capacityLabel: string;
    accessible: boolean;
  }[];
  recipients: { id: string; label: string; reachable: boolean }[];
  slots: { id: string; label: string }[];
  items: Record<string, ItemDetails>;
}
export type ProposedInventoryIntent =
  | { kind: "equip"; item_id: string; slot_id: string }
  | { kind: "drop"; item_id: string; quantity: number }
  | {
      kind: "transfer";
      item_id: string;
      quantity: number;
      recipient_actor_id: string;
    }
  | { kind: "store"; item_id: string; quantity: number; container_id: string };
export type InventoryIntent =
  | ProposedInventoryIntent
  | { kind: "inspect"; target_id: string }
  | { kind: "use_item"; item_id: string; quantity: number };
export type InventoryCommand = Omit<
  SubmitAction,
  "intent" | "expected_versions"
> & {
  intent: ProposedInventoryIntent;
  expected_versions: SubmitAction["expected_versions"] & { inventory: string };
};
export interface InventoryPreviewTransport {
  /** Deliberate preview seam until proposed intents are promoted and #49 integrates them. */
  submitInventory(
    campaignId: string,
    request: InventoryCommand,
    signal: AbortSignal,
  ): Promise<Action>;
}
export const operationLabel: Record<InventoryOperation, string> = {
  inspect: "Inspect",
  use_item: "Use",
  equip: "Equip",
  drop: "Drop",
  transfer: "Transfer",
  store: "Store",
};
