/** Presentation port for #56; deliberately outside the frozen v1 wire contract. */
export type Identity = "host" | "guest";
export interface Setup {
  name: string;
  premise: string;
  tone: "hopeful" | "gritty";
  duration: "one-shot" | "short-campaign";
  difficulty: "standard" | "challenging";
  rules: "wayfarer-lite-1";
  party: "companions" | "strangers";
  scenario: "courier" | "lighthouse";
}
export const defaultSetup: Setup = {
  name: "The Missing Courier",
  premise: "Find the courier before dawn.",
  tone: "hopeful",
  duration: "one-shot",
  difficulty: "standard",
  rules: "wayfarer-lite-1",
  party: "companions",
  scenario: "courier",
};
export interface Build {
  name: string;
  concept: string;
  strength: number;
  dexterity: number;
}
export interface Draft {
  revision: number;
  build: Build;
  spent: number;
  remaining: number;
  errors: string[];
  status: "illegal" | "pending" | "approved" | "finalized";
}
export interface CharacterSlot {
  id: string;
  label: string;
  owner: Identity | null;
  draft: Draft | null;
  history: Draft[];
}
export interface Lobby {
  revision: number;
  setup: Setup;
  status: "draft" | "active";
  members: { id: Identity; ready: boolean }[];
  characters: CharacterSlot[];
  preview: { title: string; description: string };
  invite: string | null;
}
export interface OnboardingView {
  lobby: Lobby | null;
}
export type Intent =
  | { kind: "create"; setup: Setup }
  | { kind: "join"; token: string }
  | { kind: "invite" }
  | { kind: "assign"; slot: string; owner: Identity }
  | { kind: "claim"; slot: string }
  | { kind: "save"; slot: string; build: Build; draftRevision: number }
  | {
      kind: "generate";
      slot: string;
      prompt: string;
      draftRevision: number;
      outcome: "success" | "failure" | "invalid";
    }
  | { kind: "approve" | "finalize"; slot: string; draftRevision: number }
  | { kind: "ready"; ready: boolean }
  | { kind: "activate" };
export interface Command {
  id: string;
  revision: number;
  intent: Intent;
}
export interface OnboardingPort {
  read(signal: AbortSignal): Promise<OnboardingView>;
  command(command: Command, signal: AbortSignal): Promise<OnboardingView>;
}
