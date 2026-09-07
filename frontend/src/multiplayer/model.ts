import type { Action, Snapshot } from "../play/transport";

/** Normalized adapter contract. Presence/group writes remain proposed v1 extensions. */
export interface Scope {
  campaignId: string;
  sceneId: string;
  actorId: string;
}
export interface Checkpoint {
  cursor: string;
  epoch: string;
}
export interface MultiplayerView {
  scope: Scope;
  checkpoint: Checkpoint;
  snapshot: Snapshot;
  actions: Action[];
  controlledActors: { id: string; name: string }[];
  presence: {
    id: string;
    name: string;
    status: "online" | "away";
    ready: boolean;
  }[];
  groupVersion: string;
  destinations: { id: string; label: string; kinds: GroupKind[] }[];
  ooc: {
    enabled: boolean;
    messages: { id: string; author: string; text: string }[];
  };
}
export type GroupKind = "split" | "transfer" | "rejoin";
export type TableIntent =
  | { kind: GroupKind; destinationId: string }
  | { kind: "ready"; ready: boolean }
  | { kind: "ooc"; text: string };
export interface TableCommand {
  commandId: string;
  scope: Scope;
  expectedGroupVersion: string;
  expectedMembershipVersion: string;
  intent: TableIntent;
}
export type ScopeEvent =
  | {
      kind: "changed";
      scope: Scope;
      checkpoint: Checkpoint;
      previousCursor: string;
    }
  | { kind: "reset" | "revoked" | "disconnected" };
export interface MultiplayerPort {
  read(
    campaignId: string,
    actorId: string | null,
    signal: AbortSignal,
  ): Promise<MultiplayerView>;
  watch(
    view: MultiplayerView,
    signal: AbortSignal,
    receive: (event: ScopeEvent) => void,
  ): void;
  command(request: TableCommand, signal: AbortSignal): Promise<void>;
}
/** An epoch is opaque; keys never mix principals, actors, scenes or visibility grants. */
export const scopeKey = (principal: string, scope: Scope, epoch: string) =>
  [
    "play",
    principal,
    scope.campaignId,
    scope.sceneId,
    scope.actorId,
    epoch,
  ] as const;
export const sameScope = (a: Scope, b: Scope) =>
  a.campaignId === b.campaignId &&
  a.sceneId === b.sceneId &&
  a.actorId === b.actorId;
