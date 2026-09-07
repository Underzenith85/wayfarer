import { describe, expect, it } from "vitest";
import { OnboardingAuthority } from "../../fixtures/onboarding-authority";
import { defaultSetup, type Identity, type Intent } from "./model";
function table() {
  const a = new OnboardingAuthority();
  let serial = 0;
  const run = (who: Identity, intent: Intent) =>
    a.command(who, {
      id: String(++serial),
      revision: a.read(who).lobby?.revision ?? 0,
      intent,
    });
  run("host", { kind: "create", setup: defaultSetup });
  run("host", { kind: "invite" });
  const token = a.read("host").lobby!.invite!;
  run("guest", { kind: "join", token });
  run("host", { kind: "claim", slot: "scout" });
  run("guest", { kind: "claim", slot: "scholar" });
  return { a, run, token };
}
const build = {
  name: "Mara",
  concept: "A careful scout",
  strength: 11,
  dexterity: 12,
};
describe("onboarding authority", () => {
  it("enforces ownership, invite membership, draft legality and approval before readiness", () => {
    const { a, run, token } = table();
    expect(() => run("guest", { kind: "join", token })).toThrow(/Invitation/);
    expect(() => run("guest", { kind: "invite" })).toThrow(/host/);
    expect(() => run("guest", { kind: "claim", slot: "scout" })).toThrow(
      /claimed/,
    );
    expect(() =>
      run("guest", { kind: "save", slot: "scout", draftRevision: 0, build }),
    ).toThrow(/owner/);
    expect(() => run("host", { kind: "ready", ready: true })).toThrow(
      /Finalize/,
    );
    run("host", {
      kind: "generate",
      slot: "scout",
      draftRevision: 0,
      prompt: "Scout",
      outcome: "invalid",
    });
    expect(a.read("host").lobby!.characters[0]!.draft?.errors).toContain(
      "Build exceeds the 100-point budget.",
    );
    expect(() =>
      run("host", { kind: "approve", slot: "scout", draftRevision: 1 }),
    ).toThrow(/legal/);
    run("host", { kind: "save", slot: "scout", draftRevision: 1, build });
    expect(() =>
      run("host", { kind: "finalize", slot: "scout", draftRevision: 2 }),
    ).toThrow(/awaiting/);
    run("host", { kind: "approve", slot: "scout", draftRevision: 2 });
    run("host", {
      kind: "save",
      slot: "scout",
      draftRevision: 2,
      build: { ...build, strength: 12 },
    });
    expect(a.read("host").lobby!.characters[0]!.draft?.status).toBe("pending");
    expect(a.read("host").lobby!.characters[0]!.history).toHaveLength(2);
    expect(a.read("guest").lobby!.characters[0]!.draft).toBeNull();
    expect(a.read("guest").lobby!.characters[0]!.history).toEqual([]);
  });
  it("preserves saved drafts on generation failure and rejects stale commands", () => {
    const { a, run } = table();
    run("host", { kind: "save", slot: "scout", build, draftRevision: 0 });
    const before = a.read("host");
    expect(() =>
      run("host", {
        kind: "generate",
        slot: "scout",
        draftRevision: 1,
        prompt: "Scout",
        outcome: "failure",
      }),
    ).toThrow(/unchanged/);
    expect(a.read("host")).toEqual(before);
    expect(() =>
      a.command("host", {
        id: "stale",
        revision: 1,
        intent: { kind: "activate" },
      }),
    ).toThrow(/lobby changed/);
    expect(() =>
      run("host", { kind: "save", slot: "scout", build, draftRevision: 0 }),
    ).toThrow(/Draft changed/);
  });
  it("starts exactly once after two approved, finalized, ready characters", () => {
    const { a, run } = table();
    expect(() => run("host", { kind: "activate" })).toThrow(/Each player/);
    for (const [who, slot] of [
      ["host", "scout"],
      ["guest", "scholar"],
    ] as const) {
      run(who, { kind: "save", slot, draftRevision: 0, build });
      expect(() =>
        run("guest", { kind: "approve", slot, draftRevision: 1 }),
      ).toThrow(/host/);
      run("host", { kind: "approve", slot, draftRevision: 1 });
      run(who, { kind: "finalize", slot, draftRevision: 1 });
      run(who, { kind: "ready", ready: true });
    }
    const command = {
      id: "activate-once",
      revision: a.read("host").lobby!.revision,
      intent: { kind: "activate" as const },
    };
    a.command("host", command);
    const active = a.read("host");
    expect(a.command("host", command)).toEqual(active);
    expect(() =>
      a.command("host", { ...command, intent: { kind: "invite" } }),
    ).toThrow(/different input/);
    expect(() => run("host", { kind: "activate" })).toThrow(
      /already activated/,
    );
    expect(a.snapshot("guest").campaign.membership.actor_ids).toEqual([
      "scholar",
    ]);
    expect(a.snapshot("host").scene.title).toBe("The courier’s cellar");
  });
  it("does not disclose an unjoined room and rejects unsupported setup or excessive disadvantages", () => {
    const a = new OnboardingAuthority();
    expect(() =>
      a.command("host", {
        id: "bad",
        revision: 0,
        intent: { kind: "create", setup: { ...defaultSetup, name: "" } },
      }),
    ).toThrow(/required/);
    a.command("host", {
      id: "good",
      revision: 0,
      intent: {
        kind: "create",
        setup: { ...defaultSetup, scenario: "lighthouse" },
      },
    });
    expect(a.read("guest")).toEqual({ lobby: null });
    expect(() => a.snapshot("guest")).toThrow(/No active/);
    const { a: b, run } = table();
    run("host", {
      kind: "save",
      slot: "scout",
      draftRevision: 0,
      build: { ...build, strength: 8, dexterity: 8 },
    });
    expect(b.read("host").lobby!.characters[0]!.draft?.errors).toContain(
      "Reduced attributes exceed the 25-point disadvantage limit.",
    );
  });
});
