import {
  TransportError,
  wait,
  type Action,
  type Campaign,
  type ClarifyAction,
  type PlayTransport,
  type SubmitAction,
} from "../play/transport";
import type {
  MultiplayerPort,
  MultiplayerView,
  ScopeEvent,
  TableCommand,
} from "./model";

/** Explicit development adapter. The authority runs in Vite, never in this bundle. */
export class MultiplayerFixtureTransport
  implements PlayTransport, MultiplayerPort
{
  readonly sample = true;
  readonly multiplayer = this;
  readonly principalId: string;
  private actorId: string | null = null;
  constructor(
    private identity: "captive" | "rescuer",
    private room: string,
  ) {
    this.principalId = `mock:${room}:${identity}`;
  }
  private async request<T>(body: unknown, signal: AbortSignal): Promise<T> {
    let response: Response;
    try {
      response = await fetch("/__fixtures/multiplayer", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-mock-identity": this.identity,
          "x-mock-room": this.room,
        },
        body: JSON.stringify(body),
        signal,
        cache: "no-store",
      });
    } catch (error) {
      if (signal.aborted) throw error;
      throw new TransportError(
        "network",
        "Connection lost. Reconnect to reconcile your scene.",
      );
    }
    const value: unknown = await response.json();
    if (!response.ok) {
      const error = value as { code: TransportError["code"]; message: string };
      throw new TransportError(error.code, error.message);
    }
    return value as T;
  }
  listCampaigns(signal: AbortSignal) {
    return this.request<Campaign[]>({ op: "campaigns" }, signal);
  }
  async read(campaignId: string, actorId: string | null, signal: AbortSignal) {
    if (campaignId !== "campaign-1")
      throw new TransportError("not_found", "Campaign unavailable.");
    const view = await this.request<MultiplayerView>(
      { op: "read", actorId },
      signal,
    );
    if (!signal.aborted) this.actorId = view.scope.actorId;
    return view;
  }
  async readSnapshot(campaignId: string, signal: AbortSignal) {
    return (await this.read(campaignId, this.actorId, signal)).snapshot;
  }
  async listActions(campaignId: string, signal: AbortSignal) {
    return (await this.read(campaignId, this.actorId, signal)).actions;
  }
  submitAction(
    _campaignId: string,
    command: SubmitAction,
    signal: AbortSignal,
  ) {
    return this.request<Action>({ op: "submit", command }, signal);
  }
  clarifyAction(
    _campaignId: string,
    actionId: string,
    command: ClarifyAction,
    signal: AbortSignal,
  ) {
    return this.request<Action>(
      { op: "clarify", actorId: this.actorId, actionId, command },
      signal,
    );
  }
  getAction(_campaignId: string, actionId: string, signal: AbortSignal) {
    return this.request<Action>(
      { op: "action", actorId: this.actorId, actionId },
      signal,
    );
  }
  async *narrate() {
    yield {
      text: "Your scene’s recorded result is available.",
      status: "complete" as const,
    };
  }
  command(command: TableCommand, signal: AbortSignal) {
    return this.request<void>({ op: "command", command }, signal);
  }
  watch(
    view: MultiplayerView,
    signal: AbortSignal,
    receive: (event: ScopeEvent) => void,
  ) {
    void (async () => {
      try {
        while (!signal.aborted) {
          await wait(700, signal);
          const event = await this.request<ScopeEvent | null>(
            {
              op: "events",
              scope: view.scope,
              cursor: view.checkpoint.cursor,
              epoch: view.checkpoint.epoch,
            },
            signal,
          );
          if (!signal.aborted && event) {
            receive(event);
            return;
          }
        }
      } catch (error) {
        if (!signal.aborted)
          receive({
            kind:
              error instanceof TransportError &&
              ["forbidden", "unauthenticated"].includes(error.code)
                ? "revoked"
                : "disconnected",
          });
      }
    })();
  }
}
