import { campaigns, fixtureAction, fixtureSnapshot } from "../play/fixtures";
import type { Snapshot, SubmitAction, Action } from "../play/transport";
export const scenarioNames = [
  "resolve",
  "clarify",
  "reject",
  "retry",
  "narration-failure",
  "expired",
  "stale",
  "join",
  "legal-character",
  "illegal-character",
  "item-use",
  "encounter",
  "split",
  "capture",
  "rescue",
  "reconnect",
  "ending",
  "revoked",
  "duplicate",
  "out-of-order",
] as const;
export type Scenario = (typeof scenarioNames)[number];
export function isScenario(value: string | null): value is Scenario {
  return scenarioNames.some((x) => x === value);
}
export const fixtureCredential = (principalId: string) =>
  `fixture-${principalId}`;
export const fixtureCommand: SubmitAction = {
  command_id: "11111111-1111-4111-8111-111111111111",
  actor_id: "hero-1",
  scene_id: "cellar-1",
  expected_versions: { scene: "s1", character: "h1", inventory: "i1" },
  intent: { kind: "use_item", item_id: "bandage-1", quantity: 1 },
};
/** Named, scripted views. No dice, legality, inventory arithmetic or world simulation. */
export function scenarioSnapshot(
  scenario: Scenario,
  campaignId: string,
  settled = false,
  principalId = "player-1",
): Snapshot {
  const snapshot = fixtureSnapshot(campaignId, settled);
  snapshot.campaign.membership.principal_id = principalId;
  if (principalId === "gm") snapshot.campaign.membership.role = "gm";
  if (principalId === "player-2") {
    const actor = "rescuer-1";
    snapshot.campaign.membership.actor_ids = [actor];
    snapshot.scene = {
      ...snapshot.scene,
      id: "forest-1",
      title: "The forest trail",
      description: "Tracks lead north. The courier is missing.",
      observations: [],
      visible_actor_ids: [actor],
    };
    snapshot.characters = snapshot.characters.map((x) => ({
      ...x,
      id: actor,
      name: "Orin",
    }));
    snapshot.inventories = snapshot.inventories.map((x) => ({
      ...x,
      actor_id: actor,
      items: [],
      total_weight_grams: 0,
    }));
    snapshot.party = [{ id: actor, name: "Orin", status: "In this scene" }];
  }
  if (scenario === "capture" && principalId === "player-1") {
    snapshot.scene.description =
      "You are held in the cellar. A loose hinge may offer a way out.";
    snapshot.characters[0]!.conditions = [
      {
        id: "restrained",
        label: "Restrained",
        description: "Your hands are bound.",
      },
    ];
    snapshot.inventories[0]!.items = snapshot.inventories[0]!.items.map(
      (x) => ({
        ...x,
        location: "confiscated",
        container_id: null,
        allowed_actions: ["inspect"],
      }),
    );
  }
  if (scenario === "rescue" && settled)
    snapshot.scene.description =
      "The door opens. You can leave together; private discoveries are still private.";
  if (scenario === "ending") {
    snapshot.campaign.status = "completed";
    snapshot.session = {
      ...snapshot.session!,
      status: "completed",
      ended_at: "2026-09-06T23:00:00Z",
      summary:
        "The courier returned safely. The recorded reward was settled once.",
    };
  }
  if (scenario === "encounter")
    snapshot.scene.description =
      "A guard blocks the door. Choose your response.";
  return snapshot;
}
export function scenarioAction(
  scenario: Scenario,
  request: SubmitAction,
  id: string,
  status: Action["status"],
): Action {
  const action = fixtureAction(request, id, status);
  if (action.status === "needs_clarification" && scenario === "encounter")
    action.clarification = {
      id: "defense-1",
      prompt: "Choose your defense.",
      choices: [{ id: "dodge", label: "Dodge" }],
      allows_text: false,
    };
  if (action.status === "succeeded" && scenario === "rescue")
    action.resolution.summary =
      "The rescue is complete. Recovered equipment is accounted for once.";
  if (action.status === "rejected" && scenario === "illegal-character")
    action.error.message =
      "Character proposal exceeds the campaign point budget. No character was activated.";
  return action;
}
export { campaigns };
