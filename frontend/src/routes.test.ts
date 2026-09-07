import { expect, it } from "vitest";
import { pagePath, parsePath } from "./routes";
it("addresses every view with and without a campaign", () => {
  expect(pagePath(null, "")).toBe("/");
  expect(pagePath(null, "character")).toBe("/character");
  expect(pagePath("courier-1", "")).toBe("/c/courier-1");
  expect(pagePath("courier-1", "journal")).toBe("/c/courier-1/journal");
  expect(pagePath("a/b", "journal")).toBe("/c/a%2Fb/journal");
});
it("reads the campaign and the view back out of a path", () => {
  expect(parsePath("/")).toEqual({ campaignId: null, segment: "" });
  expect(parsePath("/inventory")).toEqual({
    campaignId: null,
    segment: "inventory",
  });
  expect(parsePath("/c/courier-1")).toEqual({
    campaignId: "courier-1",
    segment: "",
  });
  expect(parsePath("/c/courier-1/")).toEqual({
    campaignId: "courier-1",
    segment: "",
  });
  expect(parsePath(pagePath("a/b", "campaign"))).toEqual({
    campaignId: "a/b",
    segment: "campaign",
  });
});
it("reports paths outside the companion", () => {
  expect(parsePath("/nowhere")).toBeNull();
  expect(parsePath("/c")).toBeNull();
  expect(parsePath("/c/courier-1/nowhere")).toBeNull();
  expect(parsePath("/character/extra")).toBeNull();
  expect(parsePath("/c/%E0%A4%A/journal")).toBeNull();
});
