import type { components } from "./contracts.generated";
import type { ServerMessage } from "./events.generated";
import { createApiClient } from "./client";
import { liveMessages } from "./live";
import {
  TransportError,
  type PlayTransport,
  type Snapshot,
  type Action,
  type SubmitAction,
  type ClarifyAction,
  type Narration,
} from "../play/transport";
type Schemas = components["schemas"];
interface Result<T> {
  data?: T;
  error?: Schemas["Error"];
}
function data<T>(result: Result<T>): T {
  if (result.error)
    throw new TransportError(result.error.code, result.error.message);
  if (result.data === undefined)
    throw new TransportError("network", "No response data.");
  return result.data;
}
export interface NetworkOptions {
  initialCampaignId?: string;
  origin: string;
  credential: string;
  principalId: string;
  sample?: boolean;
}
export class NetworkPlayTransport implements PlayTransport {
  readonly initialCampaignId?: string;
  readonly principalId: string;
  readonly sample: boolean;
  readonly api: ReturnType<typeof createApiClient>;
  constructor(private options: NetworkOptions) {
    if (options.initialCampaignId)
      this.initialCampaignId = options.initialCampaignId;
    this.principalId = options.principalId;
    this.sample = options.sample ?? false;
    this.api = createApiClient(`${options.origin}/api/v1`, options.credential);
  }
  private async pages<T>(
    fetchPage: (
      cursor?: string,
    ) => Promise<{ items: T[]; next_cursor: string | null }>,
  ): Promise<T[]> {
    const items: T[] = [];
    const seen = new Set<string>();
    let cursor: string | undefined;
    do {
      const page = await fetchPage(cursor);
      items.push(...page.items);
      if (!page.next_cursor) return items;
      if (seen.has(page.next_cursor) || seen.size >= 100)
        throw new Error("Invalid pagination loop");
      seen.add(page.next_cursor);
      cursor = page.next_cursor;
    } while (cursor);
    return items;
  }
  listCampaigns(signal: AbortSignal) {
    return this.pages(async (cursor) =>
      data(
        await this.api.GET("/campaigns", {
          signal,
          params: { query: cursor ? { cursor } : {} },
        }),
      ),
    );
  }
  private async scope(campaignId: string, signal: AbortSignal) {
    const campaign = data(
      await this.api.GET("/campaigns/{campaign_id}", {
        signal,
        params: { path: { campaign_id: campaignId } },
      }),
    );
    const actor_id = campaign.membership.actor_ids[0];
    const scenes = await this.pages(async (cursor) =>
      data(
        await this.api.GET("/campaigns/{campaign_id}/scenes", {
          signal,
          params: {
            path: { campaign_id: campaignId },
            query: cursor ? { cursor } : {},
          },
        }),
      ),
    );
    const scene = scenes.find(
      (s) => actor_id && s.visible_actor_ids.includes(actor_id),
    );
    if (!actor_id || !scene)
      throw new TransportError("not_found", "No authorized player scene.");
    return { campaign_id: campaignId, scene_id: scene.id, actor_id };
  }
  private async *stream(campaignId: string, signal: AbortSignal) {
    yield* liveMessages({
      url: `${this.options.origin.replace(/^http/, "ws")}/api/v1/live`,
      credential: this.options.credential,
      principalId: this.principalId,
      scope: await this.scope(campaignId, signal),
      signal,
    });
  }
  async readSnapshot(
    campaignId: string,
    signal: AbortSignal,
  ): Promise<Snapshot> {
    let begin: Extract<ServerMessage, { type: "snapshot.begin" }> | undefined;
    const resources: Extract<
      ServerMessage,
      { type: "snapshot.resource" }
    >["resource"][] = [];
    let end: Extract<ServerMessage, { type: "snapshot.end" }> | undefined;
    const keys = new Set<string>();
    for await (const message of this.stream(campaignId, signal)) {
      if (message.type === "subscription.revoked")
        throw new TransportError("forbidden", "Scene access was revoked.");
      if (message.type === "stream.reset")
        throw new TransportError(
          "stale_version",
          "Scene changed during synchronization. Retry from a fresh snapshot.",
        );
      if (message.type === "error")
        throw new TransportError(
          message.code === "unauthenticated" ? "unauthenticated" : "not_found",
          "Live snapshot unavailable.",
        );
      if (message.type === "snapshot.begin") {
        if (begin) throw new Error("Overlapping snapshots");
        begin = message;
      }
      if (message.type === "snapshot.resource") {
        if (
          !begin ||
          end ||
          message.snapshot_id !== begin.snapshot_id ||
          message.index !== resources.length ||
          message.visibility_epoch !== begin.visibility_epoch
        )
          throw new Error("Invalid snapshot sequence");
        const resource = message.resource;
        const id =
          resource.kind === "inventory"
            ? resource.value.actor_id
            : resource.value.id;
        const key = `${resource.kind}:${id}`;
        if (keys.has(key)) throw new Error("Duplicate snapshot resource");
        keys.add(key);
        resources.push(resource);
        if (JSON.stringify(resources).length > 33554432)
          throw new Error("Snapshot limit exceeded");
      }
      if (message.type === "snapshot.end") {
        if (
          !begin ||
          message.snapshot_id !== begin.snapshot_id ||
          message.resource_count !== resources.length ||
          message.resource_count !== begin.resource_count ||
          message.cursor !== begin.cursor ||
          message.visibility_epoch !== begin.visibility_epoch
        )
          throw new Error("Incomplete snapshot");
        end = message;
      }
      if (
        message.type === "action.updated" ||
        message.type === "projection.invalidated"
      )
        throw new TransportError(
          "stale_version",
          "State changed during snapshot handoff. Retry synchronization.",
        );
      if (message.type === "stream.ready") {
        if (
          !end ||
          message.cursor !== end.cursor ||
          message.visibility_epoch !== end.visibility_epoch
        )
          throw new Error("Snapshot not complete before ready");
        const campaign = resources.find((x) => x.kind === "campaign")?.value;
        const scene = resources.find((x) => x.kind === "scene")?.value;
        const characters = resources
          .filter((x) => x.kind === "character")
          .map((x) => x.value);
        const inventories = resources
          .filter((x) => x.kind === "inventory")
          .map((x) => x.value);
        if (
          !campaign ||
          !scene ||
          characters.length !== 1 ||
          inventories.length !== 1 ||
          campaign.id !== campaignId ||
          scene.id !== message.scope.scene_id ||
          characters[0]!.id !== message.scope.actor_id ||
          inventories[0]!.actor_id !== message.scope.actor_id
        )
          throw new Error("Snapshot identity mismatch");
        const sessionResult = await this.api.GET(
          "/campaigns/{campaign_id}/sessions/current",
          { signal, params: { path: { campaign_id: campaignId } } },
        );
        const session =
          sessionResult.response.status === 404 ? null : data(sessionResult);
        return {
          campaign,
          scene,
          characters,
          inventories,
          session,
          objectives: [campaign.premise],
          party: characters.map((x) => ({
            id: x.id,
            name: x.name,
            status: "In this scene",
          })),
        };
      }
    }
    throw new TransportError(
      "network",
      "Connection closed before snapshot completion.",
    );
  }
  async submitAction(
    campaignId: string,
    request: SubmitAction,
    signal: AbortSignal,
  ) {
    return data(
      await this.api.POST("/campaigns/{campaign_id}/actions", {
        signal,
        params: { path: { campaign_id: campaignId } },
        body: request,
      }),
    );
  }
  async clarifyAction(
    campaignId: string,
    actionId: string,
    request: ClarifyAction,
    signal: AbortSignal,
  ) {
    return data(
      await this.api.POST(
        "/campaigns/{campaign_id}/actions/{action_id}/clarifications",
        {
          signal,
          params: { path: { campaign_id: campaignId, action_id: actionId } },
          body: request,
        },
      ),
    );
  }
  async listActions(
    campaignId: string,
    signal: AbortSignal,
  ): Promise<Action[]> {
    const scope = await this.scope(campaignId, signal);
    return this.pages(async (cursor) =>
      data(
        await this.api.GET("/campaigns/{campaign_id}/actions", {
          signal,
          params: {
            path: { campaign_id: campaignId },
            query: { scene_id: scope.scene_id, ...(cursor ? { cursor } : {}) },
          },
        }),
      ),
    );
  }
  async getAction(campaignId: string, actionId: string, signal: AbortSignal) {
    return data(
      await this.api.GET("/campaigns/{campaign_id}/actions/{action_id}", {
        signal,
        params: { path: { campaign_id: campaignId, action_id: actionId } },
      }),
    );
  }
  async *narrate(
    campaignId: string,
    actionId: string,
    signal: AbortSignal,
  ): AsyncIterable<Narration> {
    let narration: string | undefined;
    const chunks: string[] = [];
    for await (const message of this.stream(campaignId, signal)) {
      if (
        message.type === "subscription.revoked" ||
        message.type === "stream.reset"
      )
        throw new TransportError("forbidden", "Narration access changed.");
      if (
        message.type === "narration.started" &&
        message.action_id === actionId
      )
        narration = message.narration_id;
      if (
        message.type === "narration.delta" &&
        message.narration_id === narration
      ) {
        if (
          message.index < chunks.length &&
          chunks[message.index] === message.text
        )
          continue;
        if (message.index !== chunks.length) throw new Error("Narration gap");
        chunks.push(message.text);
        yield { text: chunks.join(""), status: "provisional" };
      }
      if (
        message.type === "narration.ended" &&
        message.narration_id === narration
      ) {
        if (
          message.status === "failed" ||
          message.chunk_count !== chunks.length
        )
          throw new Error("Narration unavailable");
        yield { text: chunks.join(""), status: "complete" };
        return;
      }
    }
    throw new TransportError("network", "Narration stream ended early.");
  }
}
