import { randomUUID } from "node:crypto";
import { TransportError, type Snapshot } from "../src/play/transport";
import { fixtureSnapshot } from "../src/play/fixtures";
import type {
  Build,
  Command,
  Draft,
  Identity,
  Lobby,
  OnboardingView,
  Setup,
} from "../src/onboarding/model";
const previews = {
  courier: {
    title: "The courier’s cellar",
    description:
      "Rain taps the narrow window. Your party gathers to find the missing courier.",
  },
  lighthouse: {
    title: "The silent quay",
    description:
      "The harbor is still. Across the water, the lighthouse stands dark.",
  },
};
function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new TransportError("illegal_action", message);
}
function validate(build: Build, revision: number): Draft {
  const spent = (build.strength - 10) * 10 + (build.dexterity - 10) * 20;
  const errors: string[] = [];
  if (!build.name?.trim() || build.name.length > 80)
    errors.push("Name must contain 1–80 characters.");
  if (!build.concept?.trim() || build.concept.length > 4000)
    errors.push("Add a concept of up to 4000 characters.");
  for (const value of [build.strength, build.dexterity])
    if (!Number.isInteger(value) || value < 8 || value > 14)
      errors.push("Attributes must be whole numbers from 8 to 14.");
  if (spent > 100) errors.push("Build exceeds the 100-point budget.");
  if (
    Math.min(0, build.strength - 10) * 10 +
      Math.min(0, build.dexterity - 10) * 20 <
    -25
  )
    errors.push("Reduced attributes exceed the 25-point disadvantage limit.");
  return {
    revision,
    build: structuredClone(build),
    spent,
    remaining: 100 - spent,
    errors,
    status: errors.length ? "illegal" : "pending",
  };
}
function validateSetup(s: Setup) {
  requireValue(
    s &&
      s.name?.trim() &&
      s.name.length <= 80 &&
      s.premise?.trim() &&
      s.premise.length <= 2000,
    "Name and premise are required and must fit their limits.",
  );
  requireValue(
    ["hopeful", "gritty"].includes(s.tone) &&
      ["one-shot", "short-campaign"].includes(s.duration) &&
      ["standard", "challenging"].includes(s.difficulty) &&
      s.rules === "wayfarer-lite-1" &&
      ["companions", "strangers"].includes(s.party) &&
      Object.hasOwn(previews, s.scenario),
    "Choose supported campaign settings.",
  );
}
/** Shared dev-server authority. Never import this into the client bundle. */
export class OnboardingAuthority {
  private lobby: Lobby | null = null;
  private receipts = new Map<string, string>();
  read(who: Identity): OnboardingView {
    if (!this.lobby?.members.some((m) => m.id === who)) return { lobby: null };
    const lobby = structuredClone(this.lobby);
    if (who !== "host") lobby.invite = null;
    for (const slot of lobby.characters) {
      if (who !== "host" && slot.owner !== who) {
        slot.draft = null;
        slot.history = [];
      }
    }
    return { lobby };
  }
  command(who: Identity, c: Command): OnboardingView {
    requireValue(
      typeof c.id === "string" && c.id.length > 0 && c.id.length <= 100,
      "Command ID is required.",
    );
    const key = `${who}:${c.id}`,
      fingerprint = JSON.stringify(c);
    if (this.receipts.has(key)) {
      requireValue(
        this.receipts.get(key) === fingerprint,
        "Command ID was already used for different input.",
      );
      return this.read(who);
    }
    const i = c.intent;
    if (i.kind === "join") {
      requireValue(
        this.lobby?.status === "draft" &&
          this.lobby.invite !== null &&
          i.token === this.lobby.invite,
        "Invitation is invalid or no longer available.",
      );
      requireValue(
        !this.lobby.members.some((m) => m.id === who),
        "You already joined this campaign.",
      );
      this.lobby.members.push({ id: who, ready: false });
      this.lobby.invite = null;
      this.lobby.revision++;
    } else if (i.kind === "create") {
      requireValue(who === "host", "Only the mock host can create a campaign.");
      requireValue(
        !this.lobby && c.revision === 0,
        "A campaign already exists in this room.",
      );
      validateSetup(i.setup);
      this.lobby = {
        revision: 1,
        setup: structuredClone(i.setup),
        status: "draft",
        members: [{ id: who, ready: false }],
        characters: ["scout", "scholar"].map((id) => ({
          id,
          label: id === "scout" ? "Scout" : "Scholar",
          owner: null,
          draft: null,
          history: [],
        })),
        preview: previews[i.setup.scenario],
        invite: null,
      };
    } else {
      const lobby = this.lobby;
      if (!lobby?.members.some((m) => m.id === who))
        throw new TransportError("forbidden", "Join this campaign first.");
      if (c.revision !== lobby.revision)
        throw new TransportError(
          "stale_version",
          "The lobby changed. Refresh and review before retrying.",
        );
      requireValue(lobby.status === "draft", "Campaign already activated.");
      const host = () => {
        if (who !== "host")
          throw new TransportError("forbidden", "Only the host can do that.");
      };
      if (i.kind === "invite") {
        host();
        lobby.invite = randomUUID();
      } else if (i.kind === "activate") {
        host();
        requireValue(
          lobby.members.length >= 2 &&
            lobby.members.every(
              (m) =>
                m.ready &&
                lobby.characters.some(
                  (s) => s.owner === m.id && s.draft?.status === "finalized",
                ),
            ),
          "Each player must finalize a character and mark ready.",
        );
        lobby.status = "active";
        lobby.invite = null;
      } else if (i.kind === "ready") {
        requireValue(
          !i.ready ||
            lobby.characters.some(
              (s) => s.owner === who && s.draft?.status === "finalized",
            ),
          "Finalize your character before marking ready.",
        );
        lobby.members.find((m) => m.id === who)!.ready = i.ready;
      } else {
        const slot = lobby.characters.find((s) => s.id === i.slot);
        requireValue(slot, "Character slot does not exist.");
        if (i.kind === "claim" || i.kind === "assign") {
          if (i.kind === "assign") host();
          const owner = i.kind === "claim" ? who : i.owner;
          requireValue(
            lobby.members.some((m) => m.id === owner),
            "The player must join first.",
          );
          requireValue(!slot.owner, "This character has already been claimed.");
          requireValue(
            !lobby.characters.some((s) => s.owner === owner),
            "This player already has a character.",
          );
          slot.owner = owner;
        } else {
          if (i.kind === "approve") host();
          else if (slot.owner !== who)
            throw new TransportError(
              "forbidden",
              "Only the character owner can edit or finalize this draft.",
            );
          requireValue(slot.owner, "Claim or assign the character first.");
          requireValue(
            i.draftRevision === (slot.draft?.revision ?? 0),
            "Draft changed. Refresh and review the latest revision.",
          );
          if (i.kind === "save" || i.kind === "generate") {
            requireValue(
              slot.draft?.status !== "finalized",
              "Finalized characters cannot be edited.",
            );
            let build: Build;
            if (i.kind === "generate") {
              requireValue(
                i.prompt.trim() && i.prompt.length <= 4000,
                "Add a character concept first.",
              );
              if (i.outcome === "failure")
                throw new TransportError(
                  "service_unavailable",
                  "Character generation failed. Your saved draft is unchanged; retry or edit manually.",
                );
              build = {
                name: slot.draft?.build.name ?? slot.label,
                concept: i.prompt,
                strength: i.outcome === "invalid" ? 14 : 11,
                dexterity: i.outcome === "invalid" ? 14 : 12,
              };
            } else build = i.build;
            const draft = validate(build, i.draftRevision + 1);
            if (slot.draft) slot.history.push(structuredClone(slot.draft));
            slot.draft = draft;
          } else {
            const draft = slot.draft;
            requireValue(
              draft && !draft.errors.length,
              "Save a legal draft before approval or finalization.",
            );
            requireValue(
              draft.status === (i.kind === "approve" ? "pending" : "approved"),
              "This draft is not awaiting this operation.",
            );
            draft.status = i.kind === "approve" ? "approved" : "finalized";
          }
          lobby.members.find((m) => m.id === slot.owner)!.ready = false;
        }
      }
      lobby.revision++;
    }
    this.receipts.set(key, fingerprint);
    return this.read(who);
  }
  snapshot(who: Identity): Snapshot {
    const lobby = this.read(who).lobby;
    if (!lobby || lobby.status !== "active")
      throw new TransportError("not_found", "No active campaign.");
    const s = fixtureSnapshot("campaign-1");
    const slot = lobby.characters.find((c) => c.owner === who)!;
    s.campaign = {
      ...s.campaign,
      name: lobby.setup.name,
      premise: lobby.setup.premise,
      version: String(lobby.revision),
      capabilities: [],
      membership: {
        ...s.campaign.membership,
        principal_id: who,
        role: who === "host" ? "gm" : "player",
        actor_ids: [slot.id],
      },
    };
    s.scene = {
      ...s.scene,
      ...lobby.preview,
      visible_actor_ids: lobby.characters.map((c) => c.id),
      observations: [],
    };
    s.characters = [
      { ...s.characters[0]!, id: slot.id, name: slot.draft!.build.name },
    ];
    s.inventories = [];
    s.session = null;
    s.objectives = [lobby.setup.premise];
    s.party = lobby.characters.map((c) => ({
      id: c.id,
      name: c.label,
      status: "Ready",
    }));
    return s;
  }
}
