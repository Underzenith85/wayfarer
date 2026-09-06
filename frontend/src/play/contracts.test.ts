import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import schemas from "../../../contracts/v1/schemas.json";
import { campaigns, fixtureAction, fixtureSnapshot } from "./fixtures";
import type { SubmitAction } from "./transport";
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schemas);
const valid = (name: string, data: unknown) => {
  const validate = ajv.getSchema(`${schemas.$id}#/$defs/${name}`)!;
  expect(validate(data), JSON.stringify(validate.errors)).toBe(true);
};
const command: SubmitAction = {
  command_id: "11111111-1111-4111-8111-111111111111",
  actor_id: "hero-1",
  scene_id: "cellar-1",
  expected_versions: { scene: "s1", character: "h1", inventory: "i1" },
  intent: { kind: "text", text: "Use bandage" },
};
describe("UI fixture compatibility with frozen v1 schemas", () => {
  it("validates both campaigns and initial/committed authorized projections", () => {
    for (const campaign of campaigns) {
      valid("Campaign", campaign);
      for (const committed of [false, true]) {
        const snapshot = fixtureSnapshot(campaign.id, committed);
        valid("Scene", snapshot.scene);
        valid("Session", snapshot.session);
        snapshot.characters.forEach((c) => valid("Character", c));
        snapshot.inventories.forEach((i) => valid("Inventory", i));
      }
    }
  });
  it("validates commands and every action lifecycle fixture", () => {
    valid("SubmitAction", command);
    for (const state of [
      "submitted",
      "needs_clarification",
      "resolving",
      "succeeded",
      "rejected",
      "cancelled",
    ] as const)
      valid("Action", fixtureAction(command, "action-1", state));
  });
});
