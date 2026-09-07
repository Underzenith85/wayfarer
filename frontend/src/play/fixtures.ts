import { enrichSnapshot } from "../character/sample-data";
import type { components } from "../api/contracts.generated";
import {
  TransportError,
  wait,
  type Action,
  type Campaign,
  type ClarifyAction,
  type PlayTransport,
  type Snapshot,
  type SubmitAction,
} from "./transport";
export type Journey =
  | "resolve"
  | "clarify"
  | "reject"
  | "retry"
  | "narration-failure"
  | "expired"
  | "stale";
const timestamp = "2026-09-06T22:00:00Z";
const time = { ticks: 12, tick_duration_ms: 1000 };
export const campaigns: Campaign[] = [
  {
    id: "campaign-1",
    name: "The Missing Courier",
    premise: "Find the courier before dawn.",
    status: "active",
    version: "c1",
    game_time: time,
    membership: {
      principal_id: "player-1",
      role: "player",
      actor_ids: ["hero-1"],
      version: "m1",
      campaign_id: "campaign-1",
    },
    capabilities: [
      "actions.text",
      "actions.question",
      "actions.inspect",
      "actions.use_item",
    ],
    updated_at: timestamp,
  },
  {
    id: "campaign-2",
    name: "Lights on the Sound",
    premise: "Discover why the lighthouse has gone dark.",
    status: "paused",
    version: "c1",
    game_time: time,
    membership: {
      principal_id: "player-1",
      role: "player",
      actor_ids: ["hero-2"],
      version: "m1",
      campaign_id: "campaign-2",
    },
    capabilities: ["actions.text", "actions.question", "actions.inspect"],
    updated_at: timestamp,
  },
];
export function fixtureSnapshot(id: string, committed = false): Snapshot {
  const campaign = campaigns.find((c) => c.id === id);
  if (!campaign) throw new TransportError("not_found", "Campaign unavailable.");
  const second = id === "campaign-2",
    actor = second ? "hero-2" : "hero-1";
  return structuredClone({
    campaign,
    scene: {
      id: second ? "quay-1" : "cellar-1",
      version: committed ? "s2" : "s1",
      title: second ? "The silent quay" : "The courier’s cellar",
      description: second
        ? "The harbor is still. Across the water, the lighthouse stands dark."
        : "Rain taps the narrow window. A locked door separates you from the street above.",
      game_time: time,
      observations: [
        {
          id: second ? "lantern-1" : "door-1",
          label: second ? "Unlit lantern" : "Locked door",
          description: second
            ? "A cold brass lantern hangs from a hook."
            : "Fresh scratches mark the lock.",
        },
      ],
      visible_actor_ids: [actor],
    },
    characters: [
      {
        id: actor,
        campaign_id: id,
        name: second ? "Ivo" : "Mara",
        version: committed ? "h2" : "h1",
        hp: { current: committed ? 9 : 8, maximum: 10 },
        fp: { current: 9, maximum: 10 },
        attributes: [],
        skills: [],
        defenses: [],
        movement: [],
        conditions: [],
        equipped_item_ids: [],
      },
    ],
    inventories: [
      {
        actor_id: actor,
        version: committed ? "i2" : "i1",
        items: [
          {
            id: "bandage-1",
            name: "Bandage",
            description: "Clean linen",
            quantity: committed ? 1 : 2,
            unit_weight_grams: 50,
            location: "carried",
            container_id: null,
            allowed_actions: ["inspect", "use_item"],
          },
        ],
        total_weight_grams: committed ? 50 : 100,
        encumbrance: "none",
      },
    ],
    session: {
      id: second ? "session-2" : "session-1",
      campaign_id: id,
      status: second ? "paused" : "active",
      summary: second
        ? "You reached the harbor before sunset. The keeper has not returned."
        : "The courier’s last delivery led to this cellar. You found a scrap of blue cloth by the window.",
      started_at: timestamp,
      ended_at: null,
    },
    objectives: [campaign.premise],
    party: [
      { id: actor, name: second ? "Ivo" : "Mara", status: "In this scene" },
    ],
  });
}
export function fixtureAction(
  command: SubmitAction,
  id: string,
  status: Action["status"],
): Action {
  const common = {
    id,
    command_id: command.command_id,
    actor_id: command.actor_id,
    scene_id: command.scene_id,
    version: `a_${status}`,
    created_at: timestamp,
    updated_at: timestamp,
  };
  switch (status) {
    case "needs_clarification":
      return {
        ...common,
        status,
        clarification: {
          id: "clarification-1",
          prompt: "What would you like to focus on?",
          choices: [{ id: "door-1", label: "The locked door" }],
          allows_text: true,
        },
      };
    case "succeeded":
      return {
        ...common,
        status,
        resolution: {
          summary:
            "The recorded action is complete. Mara’s wound is dressed; one bandage remains.",
          checks: [
            {
              label: "First Aid",
              dice: [2, 3, 4],
              target: 12,
              margin: 3,
              outcome: "success",
            },
          ],
          changed_resources: [
            {
              resource_type: "character",
              resource_id: command.actor_id,
              version: "h2",
            },
            {
              resource_type: "inventory",
              resource_id: command.actor_id,
              version: "i2",
            },
            {
              resource_type: "scene",
              resource_id: command.scene_id,
              version: "s2",
            },
          ],
          game_time: time,
        },
      };
    case "rejected":
      return {
        ...common,
        status,
        error: {
          code: "illegal_action",
          message: "That item is not accessible from this scene.",
          retryable: false,
          request_id: command.command_id,
          field_errors: [],
        },
      };
    default:
      return { ...common, status };
  }
}
/** Scripted responses, not a rules engine: arbitrary input never determines a roll or result. */
export class FixtureTransport implements PlayTransport {
  readonly principalId = "player-1";
  readonly sample = true;
  readonly requests: {
    campaignId: string;
    actionId?: string;
    request: SubmitAction | ClarifyAction;
  }[] = [];
  private actions = new Map<
    string,
    {
      request: SubmitAction;
      campaignId: string;
      step: number;
      clarified: boolean;
    }
  >();
  private receipts = new Map<string, Action>();
  private latest = new Map<string, Action>();
  private committed = new Set<string>();
  private failedOnce = false;
  constructor(
    private journey: Journey = "resolve",
    private latency = 100,
  ) {}
  async listCampaigns(signal: AbortSignal) {
    await wait(this.latency, signal);
    return structuredClone(campaigns);
  }
  async readSnapshot(id: string, signal: AbortSignal) {
    await wait(this.latency, signal);
    return enrichSnapshot(fixtureSnapshot(id, this.committed.has(id)));
  }
  async submitAction(
    campaignId: string,
    request: SubmitAction,
    signal: AbortSignal,
  ) {
    this.requests.push({ campaignId, request: structuredClone(request) });
    await wait(this.latency, signal);
    if (this.journey === "expired")
      throw new TransportError("unauthenticated", "Session expired.");
    if (this.journey === "stale")
      throw new TransportError(
        "stale_version",
        "The scene changed. Reload the campaign and reconsider your action.",
      );
    const old = this.receipts.get(request.command_id);
    if (old) return structuredClone(old);
    const id = `action-${this.actions.size + 1}`;
    const action = fixtureAction(request, id, "submitted");
    this.actions.set(id, { request, campaignId, step: 0, clarified: false });
    this.receipts.set(request.command_id, action);
    this.latest.set(action.id, action);
    if (this.journey === "retry" && !this.failedOnce) {
      this.failedOnce = true;
      throw new TransportError(
        "network",
        "Connection lost before acknowledgement. Retry the same action safely.",
      );
    }
    return structuredClone(action);
  }
  async clarifyAction(
    campaignId: string,
    actionId: string,
    request: ClarifyAction,
    signal: AbortSignal,
  ) {
    this.requests.push({
      campaignId,
      actionId,
      request: structuredClone(request),
    });
    await wait(this.latency, signal);
    const old = this.receipts.get(request.command_id);
    if (old) return structuredClone(old);
    const record = this.actions.get(actionId);
    if (!record || record.campaignId !== campaignId)
      throw new TransportError("not_found", "Action unavailable.");
    record.clarified = true;
    record.step = 0;
    const action = fixtureAction(record.request, actionId, "submitted");
    this.receipts.set(request.command_id, action);
    this.latest.set(action.id, action);
    return action;
  }
  async listActions(campaignId: string, signal: AbortSignal) {
    await wait(this.latency, signal);
    return structuredClone(
      [...this.latest.values()].filter(
        (a) => this.actions.get(a.id)?.campaignId === campaignId,
      ),
    );
  }
  async getAction(campaignId: string, id: string, signal: AbortSignal) {
    await wait(this.latency, signal);
    const record = this.actions.get(id);
    if (!record || record.campaignId !== campaignId)
      throw new TransportError("not_found", "Action unavailable.");
    record.step++;
    let status: Action["status"];
    if (this.journey === "clarify" && !record.clarified)
      status = "needs_clarification";
    else if (record.step === 1) status = "resolving";
    else status = this.journey === "reject" ? "rejected" : "succeeded";
    if (status === "succeeded") this.committed.add(campaignId);
    const action = fixtureAction(record.request, id, status);
    this.latest.set(id, action);
    return action;
  }
  async *narrate(_campaignId: string, _actionId: string, signal: AbortSignal) {
    yield {
      text: "The linen catches the lamplight…",
      status: "provisional" as const,
    };
    await wait(this.latency * 3, signal);
    if (this.journey === "narration-failure")
      throw new Error("Provider unavailable");
    yield {
      text: "You draw the clean linen tight. Above you, footsteps pass the cellar door, then fade into the rain.",
      status: "complete" as const,
    };
  }
}
// An explicit presentation fixture boundary, not a promoted HTTP narration schema.
export type ContractFixture = components["schemas"]["Action"];
