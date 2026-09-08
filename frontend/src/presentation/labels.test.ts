import { describe, expect, it } from "vitest";
import {
  ageLabel,
  campaignPhaseLabel,
  definitionLabel,
  conditionLabel,
  encumbranceLabel,
  changedLabel,
  humanize,
  lifecycleOperationLabel,
  lifecycleReason,
  poolLabel,
  presentStats,
  sceneDescription,
  statLabel,
  timestampLabel,
} from "./labels";
describe("statLabel", () => {
  it("names engine attribute keys without exposing them", () => {
    expect(statLabel({ id: "attribute:st", label: "attribute:st" })).toEqual({
      short: "ST",
      full: "Strength",
    });
    expect(statLabel({ id: "attribute:iq", label: "attribute:iq" })).toEqual({
      short: "IQ",
      full: "Intelligence",
    });
  });
  it("resolves unprefixed keys identically", () => {
    expect(statLabel({ id: "ht", label: "HT" }).full).toBe("Health");
  });
  it("keeps a label the service already wrote in prose", () => {
    expect(statLabel({ id: "skill:first-aid", label: "First Aid" })).toEqual({
      short: "First Aid",
      full: "First Aid",
    });
  });
  it("falls back to a readable name for unknown keys", () => {
    expect(
      statLabel({ id: "skill:broadsword", label: "skill:broadsword" }),
    ).toEqual({ short: "Broadsword", full: "Broadsword" });
  });
});
describe("presentStats", () => {
  it("orders attributes ST, DX, IQ, HT rather than alphabetically", () => {
    const stats = [
      { id: "attribute:dx", label: "attribute:dx", value: 12 },
      { id: "attribute:ht", label: "attribute:ht", value: 10 },
      { id: "attribute:iq", label: "attribute:iq", value: 11 },
      { id: "attribute:st", label: "attribute:st", value: 10 },
    ];
    expect(presentStats(stats).map((s) => s.short)).toEqual([
      "ST",
      "DX",
      "IQ",
      "HT",
    ]);
  });
  it("keeps unrecognised statistics in the order received", () => {
    const stats = [
      { id: "skill:stealth", label: "Stealth", value: 13 },
      { id: "skill:first-aid", label: "First Aid", value: 12 },
    ];
    expect(presentStats(stats).map((s) => s.short)).toEqual([
      "Stealth",
      "First Aid",
    ]);
  });
});
describe("lifecycle and phase labels", () => {
  it("phrases lifecycle operations as the action taken", () => {
    expect(lifecycleOperationLabel("pause")).toBe("Pause session");
    expect(lifecycleOperationLabel("complete")).toBe("End campaign");
    expect(lifecycleOperationLabel("activate")).toBe("Start game");
  });
  it("humanises an operation the mapping does not name", () => {
    expect(lifecycleOperationLabel("hand_over")).toBe("Hand Over");
  });
  it("names campaign phases", () => {
    expect(campaignPhaseLabel("completed")).toBe("Finished");
    expect(campaignPhaseLabel("active")).toBe("In play");
  });
});
describe("conditionLabel", () => {
  it("gives prose to a condition projected as a bare enum value", () => {
    expect(
      conditionLabel({
        id: "stunned",
        label: "stunned",
        description: "stunned",
      }),
    ).toEqual({
      label: "Stunned",
      description: "Reeling; recovery is checked before acting normally.",
    });
  });
  it("prefers authored text when the service supplies it", () => {
    expect(
      conditionLabel({
        id: "bruised",
        label: "Bruised",
        description: "A tender shoulder.",
      }),
    ).toEqual({ label: "Bruised", description: "A tender shoulder." });
  });
});
describe("encumbranceLabel", () => {
  it("names known load bands", () => {
    expect(encumbranceLabel("none")).toBe("None");
    expect(encumbranceLabel("extra-heavy")).toBe("Extra-heavy");
  });
  it("reports no band for projection placeholders", () => {
    expect(
      encumbranceLabel("Not supplied by the current engine projection"),
    ).toBeNull();
    expect(encumbranceLabel("")).toBeNull();
    expect(encumbranceLabel(undefined)).toBeNull();
  });
});
describe("poolLabel", () => {
  const names: Record<string, string> = { a: "Mira", b: "Iven" };
  const name = (id: string) => names[id] ?? id;
  it("reads a runtime pool as its character and pool name", () => {
    expect(poolLabel("hp:b", name)).toBe("Iven · HP");
    expect(poolLabel("fp:a", name)).toBe("Mira · FP");
  });
  it("falls back to the pool name alone when the id carries no actor", () => {
    expect(poolLabel("hp", name)).toBe("HP");
  });
});
describe("definitionLabel", () => {
  it("names a purchasable attribute in full", () => {
    expect(definitionLabel("attribute:st")).toBe("Strength");
  });
  it("reads any engine namespace, not a fixed list of them", () => {
    expect(definitionLabel("trait:combat-reflexes")).toBe("Combat Reflexes");
    expect(definitionLabel("equipment:travel-coat")).toBe("Travel Coat");
  });
});
describe("humanize", () => {
  it("reads engine identifiers as words", () => {
    expect(humanize("attribute:basic-move")).toBe("Basic Move");
    expect(humanize("mira")).toBe("Mira");
  });
});
describe("sceneDescription", () => {
  it("drops a description that only repeats the scene name (#203)", () => {
    expect(
      sceneDescription("Stormbound Harbor", "Stormbound Harbor"),
    ).toBeNull();
    expect(
      sceneDescription("Stormbound Harbor", " stormbound harbor "),
    ).toBeNull();
    expect(sceneDescription("Stormbound Harbor", "  ")).toBeNull();
  });
  it("keeps a description the projection actually wrote", () => {
    expect(
      sceneDescription("Stormbound Harbor", "Rain hammers the empty quay."),
    ).toBe("Rain hammers the empty quay.");
  });
});
describe("timestampLabel", () => {
  const now = new Date(2026, 8, 7, 20);
  it("gives an entry from today a time and an older one a date too", () => {
    const today = timestampLabel(
      new Date(2026, 8, 7, 9, 30).toISOString(),
      now,
    );
    const older = timestampLabel(
      new Date(2026, 8, 4, 9, 30).toISOString(),
      now,
    );
    expect(today).not.toBe("");
    expect(older).not.toBe("");
    expect(older.length).toBeGreaterThan(today.length);
  });
  it("says nothing rather than something wrong about an unusable time", () => {
    expect(timestampLabel("not a time", now)).toBe("");
  });
});
describe("ageLabel", () => {
  const now = Date.parse("2026-09-07T20:00:00Z");
  const ago = (ms: number) => ageLabel(new Date(now - ms).toISOString(), now);
  it("counts a draft's age in the units a player would use (#201)", () => {
    expect(ago(5_000)).toBe("just now");
    expect(ago(60_000)).toBe("1 minute ago");
    expect(ago(20 * 60_000)).toBe("20 minutes ago");
    expect(ago(3 * 3_600_000)).toBe("3 hours ago");
    expect(ago(3 * 86_400_000)).toBe("3 days ago");
  });
  it("never reports a draft as saved in the future", () => {
    expect(ageLabel(new Date(now + 60_000).toISOString(), now)).toBe(
      "just now",
    );
  });
});
describe("lifecycleReason", () => {
  it("separates a finished adventure from a locked-out one (#297)", () => {
    expect(lifecycleReason("completed")).toContain("finished");
    expect(lifecycleReason("completed")).not.toBe(lifecycleReason("paused"));
    expect(lifecycleReason("paused")).toContain("paused");
    expect(lifecycleReason("archived")).toContain("archived");
    expect(lifecycleReason("draft")).toBe(lifecycleReason("ready"));
  });
  it("falls back to the plain condition for a phase it does not name", () => {
    expect(lifecycleReason("suspended")).toContain("not active");
  });
});
describe("changedLabel", () => {
  it("names what a turn changed without its ids or digests (#296)", () => {
    expect(
      changedLabel([
        { resource_type: "character" },
        { resource_type: "inventory" },
        { resource_type: "scene" },
      ]),
    ).toBe("your character, your inventory and this scene");
    expect(changedLabel([{ resource_type: "scene" }])).toBe("this scene");
    expect(changedLabel([])).toBe("");
  });
  it("reads an unfamiliar resource type rather than echoing the key", () => {
    expect(changedLabel([{ resource_type: "world_state" }])).toBe(
      "World State",
    );
  });
});
