// @vitest-environment node
import { describe, expect, it } from "vitest";
import { AdventureAuthority } from "../../fixtures/adventure-authority";
import { MultiplayerAuthority } from "../../fixtures/multiplayer-authority";
function setup() {
  const table = new MultiplayerAuthority("test");
  const authority = new AdventureAuthority(table);
  const view = table.read("captive", null);
  const scope = view.scope,
    epoch = view.checkpoint.epoch;
  const overview = () => authority.overview("captive", scope, epoch, null);
  const command = () => {
    const e = overview().encounter!;
    return {
      commandId: crypto.randomUUID(),
      scope,
      epoch,
      version: e.version,
      decisionId: e.decisionId,
      choiceId: e.choices[0]!.id,
      targetId: e.choices[0]!.targets[0]!.id,
    };
  };
  return { table, authority, scope, epoch, overview, command };
}
describe("proposed adventure authority", () => {
  it("commits defense once, rejects stale or illegal decisions, and resumes pending choices", () => {
    const f = setup();
    const command = f.command();
    expect(() =>
      f.authority.decide("captive", { ...command, targetId: "hidden-target" }),
    ).toThrow("permitted");
    f.authority.decide("captive", command);
    f.authority.decide("captive", command);
    expect(f.overview().objectives[0]?.progress).toBe("1 of 5 known steps");
    expect(f.overview().encounter?.mode).toBe("social");
    expect(() =>
      f.authority.decide("captive", {
        ...command,
        commandId: crypto.randomUUID(),
      }),
    ).toThrow("changed");
    expect(() =>
      f.authority.decide("captive", { ...command, choiceId: "other" }),
    ).toThrow("already used");
  });
  it("excludes undiscovered and other-character clues from search and direct access", () => {
    const f = setup();
    expect(
      f.authority.search("captive", f.scope, f.epoch, "marked", "all"),
    ).toEqual([]);
    expect(() =>
      f.authority.entry("captive", f.scope, f.epoch, "route-hero-1"),
    ).toThrow("unavailable");
    expect(() =>
      f.authority.entry("captive", f.scope, f.epoch, "place-hero-2"),
    ).toThrow("unavailable");
    expect(() =>
      f.authority.search("rescuer", f.scope, f.epoch, "", "all"),
    ).toThrow("access");
    expect(() =>
      f.authority.search(
        "captive",
        { ...f.scope, campaignId: "other" },
        f.epoch,
        "",
        "all",
      ),
    ).toThrow("changed");
  });
  it("records noncombat outcomes and only changes after the scoped recap checkpoint", () => {
    const f = setup();
    f.authority.decide("captive", f.command());
    const checkpoint = f.overview().recap.checkpoint;
    for (const mode of ["social", "investigation", "stealth", "hazard"]) {
      expect(f.overview().encounter?.mode).toBe(mode);
      f.authority.decide("captive", f.command());
    }
    expect(f.overview().encounter).toBeNull();
    expect(f.overview().objectives[0]?.status).toBe("complete");
    expect(
      f.authority.overview("captive", f.scope, f.epoch, checkpoint).recap
        .changes,
    ).toHaveLength(4);
    expect(
      f.authority.overview("captive", f.scope, f.epoch, "foreign").recap.reset,
    ).toBe(true);
    expect(
      f.authority.search("captive", f.scope, f.epoch, "marked", "clue"),
    ).toHaveLength(1);
    f.table.scenario("revoke", "captive");
    expect(() => f.overview()).toThrow("access");
  });
});
