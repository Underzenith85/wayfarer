/** Adapter for the authenticated engine facade. Versioned v1 remains a separate contract. */
import {
  TransportError,
  type PlayTransport,
  type Snapshot,
  type Campaign,
  type Action,
  type SubmitAction,
  type ClarifyAction,
} from "./transport";

export interface EngineTurn {
  id: string;
  actor_id: string;
  text: string;
  phase: string;
  committed: boolean;
  narration: string;
  narration_available: boolean;
}
export interface EngineProjection {
  shared_time: boolean;
  campaign_id: string;
  principal_id: string;
  revision: number;
  game_time: number;
  role: "player";
  actors: string[];
  characters: {
    actor_id: string;
    name: string;
    values: { target: string; value: string }[];
    spent: number;
  }[];
  inventory: {
    id: string;
    definition_id: string;
    owner_id: string;
    quantity: number;
    container_id: string | null;
    equipped: boolean;
    ready: boolean;
  }[];
  equipment: { id: string; name: string; unit_weight: number }[];
  pools: { id: string; current: number; maximum: number }[];
  status: { actor_id: string; conditions: string[] }[];
  scenes: {
    actor_id: string;
    id: string;
    title: string;
    exits: { id: string; destination_id: string }[];
  }[];
  perspectives: Record<
    string,
    {
      facts: { id: string; value: string }[];
      commitments: { id: string; description: string }[];
    }
  >;
  subgroups: {
    id: string;
    scene_id: string;
    actor_ids: string[];
    paused: boolean;
  }[];
  objectives: {
    outcome: string;
    progress: { objective_id: string; satisfied: boolean }[];
  };
  director: EngineTurn[];
  encounters: {
    id: string;
    status: string;
    turn_order: string[];
    turn_index: number;
    pending_defense: { defender_id: string; allowed: string[] } | null;
  }[];
  noncombat: {
    id: string;
    actor_id: string;
    status: string;
    pending_choices: string[];
  }[];
  recovery_choices: {
    id: string;
    actor_id: string;
    target_actor_id: string;
    kind: string;
  }[];
  captivity: { actor_id: string; released_at: number | null }[];
}
function projection(value: unknown): EngineProjection {
  if (!value || typeof value !== "object")
    throw new Error("Invalid campaign response");
  const v = value as Partial<EngineProjection>;
  if (
    typeof v.campaign_id !== "string" ||
    typeof v.principal_id !== "string" ||
    !Number.isSafeInteger(v.revision) ||
    typeof v.game_time !== "number" ||
    v.role !== "player" ||
    !Array.isArray(v.actors) ||
    !Array.isArray(v.characters) ||
    !Array.isArray(v.scenes) ||
    !Array.isArray(v.director)
  )
    throw new Error("A player campaign connection is required");
  return v as EngineProjection;
}
export class LiveTransport implements PlayTransport {
  readonly sample = false;
  private current: EngineProjection | null = null;
  constructor(
    readonly principalId: string,
    readonly campaignId: string,
    private token: string,
  ) {}
  async request(
    path: string,
    signal: AbortSignal,
    body?: object,
  ): Promise<unknown> {
    const response = await fetch(
      `/campaigns/${encodeURIComponent(this.campaignId)}${path}`,
      {
        signal,
        method: body ? "POST" : "GET",
        headers: {
          Authorization: `Bearer ${this.token}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      },
    );
    const value: unknown = await response.json();
    if (!response.ok) {
      const code =
        response.status === 401
          ? "unauthenticated"
          : response.status === 403
            ? "forbidden"
            : response.status === 404
              ? "not_found"
              : response.status === 409
                ? "stale_version"
                : "illegal_action";
      throw new TransportError(
        code,
        typeof value === "object" && value && "error" in value
          ? String(value.error)
          : "Campaign request failed",
      );
    }
    return value;
  }
  async readEngine(signal: AbortSignal) {
    const p = projection(await this.request("", signal));
    if (
      p.principal_id !== this.principalId ||
      p.campaign_id !== this.campaignId
    )
      throw new TransportError("forbidden", "Campaign identity changed");
    if (!this.current || p.revision >= this.current.revision) this.current = p;
    return this.current;
  }
  private campaign(p: EngineProjection): Campaign {
    return {
      id: p.campaign_id,
      name: p.campaign_id,
      premise: "Your active adventure",
      status: p.objectives.outcome === "ongoing" ? "active" : "completed",
      version: String(p.revision),
      game_time: { ticks: p.game_time, tick_duration_ms: 1000 },
      membership: {
        principal_id: this.principalId,
        role: "player",
        actor_ids: p.actors,
        version: "1",
        campaign_id: p.campaign_id,
      },
      capabilities: [
        "actions.text",
        "actions.question",
        "actions.inspect",
        "actions.use_item",
      ],
      updated_at: "1970-01-01T00:00:00Z",
    };
  }
  async listCampaigns(signal: AbortSignal) {
    return [this.campaign(await this.readEngine(signal))];
  }
  async readSnapshot(_id: string, signal: AbortSignal): Promise<Snapshot> {
    const p = await this.readEngine(signal),
      version = String(p.revision),
      scene = p.scenes[0],
      actor = p.actors[0] ?? "";
    const pool = (id: string) => {
      const x = p.pools.find((v) => v.id === id);
      return { current: x?.current ?? 0, maximum: x?.maximum ?? 0 };
    };
    return {
      campaign: this.campaign(p),
      scene: {
        id: scene?.id ?? "unassigned",
        version,
        title: scene?.title ?? "Current location",
        description: "",
        game_time: { ticks: p.game_time, tick_duration_ms: 1000 },
        observations: (p.perspectives[actor]?.facts ?? []).map((f) => ({
          id: f.id,
          label: f.id,
          description: f.value,
        })),
        visible_actor_ids: p.actors,
      },
      characters: p.characters.map((c) => ({
        id: c.actor_id,
        campaign_id: p.campaign_id,
        name: c.name,
        version,
        hp: pool(`hp:${c.actor_id}`),
        fp: pool(`fp:${c.actor_id}`),
        attributes: c.values
          .filter((v) => v.target.startsWith("attribute:"))
          .map((v) => ({
            id: v.target,
            label: v.target,
            value: Number(v.value),
          })),
        skills: c.values
          .filter((v) => v.target.startsWith("skill:"))
          .map((v) => ({
            id: v.target,
            label: v.target,
            value: Number(v.value),
          })),
        defenses: [],
        movement: [],
        conditions: (
          p.status.find((a) => a.actor_id === c.actor_id)?.conditions ?? []
        ).map((x) => ({ id: x, label: x, description: x })),
        equipped_item_ids: p.inventory
          .filter((i) => i.owner_id === c.actor_id && i.equipped)
          .map((i) => i.id),
      })),
      inventories: p.actors.map((a) => ({
        actor_id: a,
        version,
        items: p.inventory
          .filter((i) => i.owner_id === a)
          .map((i) => ({
            id: i.id,
            name:
              p.equipment.find((e) => e.id === i.id)?.name ?? i.definition_id,
            description: "",
            quantity: i.quantity,
            unit_weight_grams:
              p.equipment.find((e) => e.id === i.id)?.unit_weight ?? 0,
            location: i.equipped ? "equipped" : "carried",
            container_id: i.container_id,
            allowed_actions: ["inspect", "use_item"],
          })),
        total_weight_grams: p.inventory
          .filter((i) => i.owner_id === a)
          .reduce(
            (sum, i) =>
              sum +
              i.quantity *
                (p.equipment.find((e) => e.id === i.id)?.unit_weight ?? 0),
            0,
          ),
        encumbrance: "See character status",
      })),
      session: null,
      objectives: p.objectives.progress.map(
        (o) => `${o.objective_id}: ${o.satisfied ? "Complete" : "In progress"}`,
      ),
      party: p.subgroups.flatMap((g) =>
        g.actor_ids.map((id) => ({
          id,
          name: p.characters.find((c) => c.actor_id === id)?.name ?? id,
          status: `${g.scene_id}${g.paused ? " · paused" : ""}`,
        })),
      ),
    };
  }
  private action(t: EngineTurn): Action {
    const p = this.current!,
      common = {
        id: t.id,
        command_id: t.id,
        actor_id: t.actor_id,
        scene_id:
          p.scenes.find((s) => s.actor_id === t.actor_id)?.id ?? "unassigned",
        version: String(p.revision),
        created_at: "1970-01-01T00:00:00Z",
        updated_at: "1970-01-01T00:00:00Z",
      };
    if (t.committed || t.phase === "complete")
      return {
        ...common,
        mechanicallyCommitted: t.committed,
        status: "succeeded",
        resolution: {
          summary: t.committed
            ? "The engine committed this action."
            : "Question answered. No game state changed.",
          checks: [],
          changed_resources: [],
          game_time: { ticks: p.game_time, tick_duration_ms: 1000 },
        },
      };
    if (t.phase === "clarification")
      return {
        ...common,
        status: "needs_clarification",
        clarification: {
          id: t.id,
          prompt: t.narration,
          choices: [],
          allows_text: true,
        },
      };
    return { ...common, status: "resolving" };
  }
  async submitAction(_id: string, request: SubmitAction, signal: AbortSignal) {
    const intent = request.intent;
    const body =
      intent.kind === "text"
        ? { text: intent.text }
        : {
            text:
              intent.kind === "question"
                ? intent.text
                : `${intent.kind} action`,
            proposal: {
              ...intent,
              expected_revision: Number(request.expected_versions.scene),
            },
          };
    await this.request("/interpret", signal, {
      actor_id: request.actor_id,
      command_id: request.command_id,
      ...body,
    });
    return this.getAction(this.campaignId, request.command_id, signal);
  }
  async clarifyAction(
    _id: string,
    actionId: string,
    request: ClarifyAction,
    signal: AbortSignal,
  ) {
    const p = await this.readEngine(signal),
      old = p.director.find((t) => t.id === actionId);
    if (!old)
      throw new TransportError("not_found", "Pending action is unavailable");
    await this.request("/interpret", signal, {
      actor_id: old.actor_id,
      command_id: request.command_id,
      text:
        "text" in request.answer
          ? request.answer.text
          : request.answer.choice_id,
    });
    return this.getAction(this.campaignId, request.command_id, signal);
  }
  async listActions(_id: string, signal: AbortSignal) {
    return (await this.readEngine(signal)).director.map((t) => this.action(t));
  }
  async getAction(_id: string, id: string, signal: AbortSignal) {
    let p = await this.readEngine(signal),
      t = p.director.find((t) => t.id === id);
    if (!t) throw new TransportError("not_found", "Action unavailable");
    if (t.phase !== "complete" && t.phase !== "clarification") {
      await this.request("/interpret", signal, {
        actor_id: t.actor_id,
        command_id: t.id,
        text: t.text,
      });
      p = await this.readEngine(signal);
      t = p.director.find((t) => t.id === id)!;
    }
    return this.action(t);
  }
  async *narrate(_id: string, id: string, signal: AbortSignal) {
    const t = (await this.readEngine(signal)).director.find((t) => t.id === id);
    yield {
      text: t?.narration ?? "Narration unavailable",
      status: t?.narration_available
        ? ("complete" as const)
        : ("failed" as const),
    };
  }
  async command(
    actor_id: string,
    fields: Record<string, unknown>,
    signal: AbortSignal,
  ) {
    if (!this.current) await this.readEngine(signal);
    await this.request("/interpret", signal, {
      actor_id,
      command_id: crypto.randomUUID(),
      text: String(fields.kind),
      proposal: { ...fields, expected_revision: this.current!.revision },
    });
  }
}
