import { describe, it, expect } from "vitest";
import {
  scenarioNames,
  scenarioSnapshot,
  scenarioAction,
  fixtureCommand,
} from "./catalog";
import { parseHttp, parseServerMessage } from "../api/validation";
import events from "../../../contracts/v1/events.examples.json";
import { parseClientMessage } from "../api/validation";
describe("all named contract fixtures", () => {
  it.each(scenarioNames)(
    "%s has schema-valid views and scripted action states",
    (scenario) => {
      for (const principal of ["player-1", "player-2"])
        for (const campaign of ["campaign-1", "campaign-2"])
          for (const settled of [false, true]) {
            const snapshot = scenarioSnapshot(
              scenario,
              campaign,
              settled,
              principal,
            );
            parseHttp("Campaign", snapshot.campaign);
            parseHttp("Scene", snapshot.scene);
            if (snapshot.session) parseHttp("Session", snapshot.session);
            snapshot.characters.forEach((x) => parseHttp("Character", x));
            snapshot.inventories.forEach((x) => parseHttp("Inventory", x));
            for (const status of [
              "submitted",
              "needs_clarification",
              "resolving",
              "succeeded",
              "rejected",
              "cancelled",
            ] as const)
              parseHttp(
                "Action",
                scenarioAction(scenario, fixtureCommand, "action-1", status),
              );
          }
    },
  );
  it("validates all committed live fixtures with the same runtime validator", () => {
    for (const example of events)
      (example.direction === "client"
        ? parseClientMessage
        : parseServerMessage)(example.message);
  });
  it("rejects schema-invalid fixtures rather than bypassing validation", () => {
    const snapshot = scenarioSnapshot("resolve", "campaign-1");
    expect(() =>
      parseHttp("Character", {
        ...snapshot.characters[0],
        hp: { current: "forged", maximum: 10 },
      }),
    ).toThrow("Contract violation");
    expect(() =>
      parseHttp("SubmitAction", { ...fixtureCommand, roll: 18 }),
    ).toThrow();
    expect(() =>
      parseServerMessage({ type: "narration.delta", hp: 100 }),
    ).toThrow();
  });
  it("does not put captive-only clues in the rescuer view", () => {
    const captive = scenarioSnapshot(
      "capture",
      "campaign-1",
      false,
      "player-1",
    );
    const rescuer = scenarioSnapshot(
      "capture",
      "campaign-1",
      false,
      "player-2",
    );
    expect(captive.scene.description).toContain("loose hinge");
    expect(JSON.stringify(rescuer)).not.toContain("loose hinge");
  });
});
