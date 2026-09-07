import type { Scope } from "../multiplayer/model";
/** Normalized proposed contracts; not a claim that live engine endpoints exist. */
export type JournalKind = "npc" | "location" | "clue" | "commitment";
export interface Discovery {
  id: string;
  kind: JournalKind;
  title: string;
  text: string;
  learnedAt: string;
}
export interface Encounter {
  id: string;
  version: string;
  round: number;
  activeActor: string;
  mode: "combat" | "social" | "investigation" | "stealth" | "hazard";
  prompt: string;
  decisionId: string;
  choices: {
    id: string;
    label: string;
    trace: string;
    targets: { id: string; name: string; range: string }[];
  }[];
}
export interface AdventureView {
  encounter: Encounter | null;
  objectives: {
    id: string;
    title: string;
    progress: string;
    status: "active" | "complete";
  }[];
  recap: { checkpoint: string; changes: string[]; reset: boolean };
}
export type ClosureOutcome = "success" | "partial" | "failure";
export type CampaignLifecycle = "active" | "paused" | "completed" | "archived";
export interface SessionClosureView {
  version: string;
  session: { status: "paused" | "completed"; summary: string };
  campaign: { status: CampaignLifecycle; canContinue: boolean };
  outcome: ClosureOutcome;
  objectives: {
    id: string;
    title: string;
    outcome: "achieved" | "partial" | "failed";
    detail: string;
  }[];
  epilogue: string[];
  rewards: { id: string; label: string; detail: string }[];
  consequences: {
    id: string;
    kind: "injury" | "custody" | "commitment";
    detail: string;
  }[];
  settlement: { id: string; status: "pending" | "settled"; settledAt?: string };
  advancement: {
    id: string;
    label: string;
    options: { id: string; label: string; detail: string }[];
    selectedId?: string;
  }[];
  nextAdventure?: { title: string; premise: string; knownHook: string };
}
export interface ClosureCommand {
  commandId: string;
  scope: Scope;
  epoch: string;
  version: string;
  settlementId: string;
  selections: { advancementId: string; optionId: string }[];
}
export interface DecisionCommand {
  commandId: string;
  scope: Scope;
  epoch: string;
  version: string;
  decisionId: string;
  choiceId: string;
  targetId?: string;
}
export interface AdventurePort {
  entryHref(scope: Scope, id: string): string;
  overview(
    scope: Scope,
    epoch: string,
    since: string | null,
    signal: AbortSignal,
  ): Promise<AdventureView>;
  search(
    scope: Scope,
    epoch: string,
    query: string,
    kind: JournalKind | "all",
    signal: AbortSignal,
  ): Promise<Discovery[]>;
  entry(
    scope: Scope,
    epoch: string,
    id: string,
    signal: AbortSignal,
  ): Promise<Discovery>;
  decide(command: DecisionCommand, signal: AbortSignal): Promise<void>;
  closure(
    scope: Scope,
    epoch: string,
    signal: AbortSignal,
  ): Promise<SessionClosureView>;
  settle(
    command: ClosureCommand,
    signal: AbortSignal,
  ): Promise<SessionClosureView>;
}
