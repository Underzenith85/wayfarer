import { describe, expect, it } from "vitest";
import {
  campaignPhaseLabel,
  conditionLabel,
  encumbranceLabel,
  humanize,
  lifecycleOperationLabel,
  presentStats,
  statLabel,
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
describe("humanize", () => {
  it("reads engine identifiers as words", () => {
    expect(humanize("attribute:basic-move")).toBe("Basic Move");
    expect(humanize("mira")).toBe("Mira");
  });
});
