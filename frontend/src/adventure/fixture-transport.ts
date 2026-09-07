import { MultiplayerFixtureTransport } from "../multiplayer/fixture-transport";
import type { AdventurePort } from "./model";
export class AdventureFixtureTransport extends MultiplayerFixtureTransport {
  readonly adventure: AdventurePort = {
    entryHref: (scope, id) =>
      `/journal?${new URLSearchParams({ adventure: "true", multiplayer: this.principalId.split(":")[2]!, room: this.principalId.split(":")[1]!, campaign: scope.campaignId, actor: scope.actorId, entry: id })}`,
    overview: (scope, epoch, since, signal) =>
      this.request({ op: "adventure-overview", scope, epoch, since }, signal),
    search: (scope, epoch, query, kind, signal) =>
      this.request(
        { op: "adventure-search", scope, epoch, query, kind },
        signal,
      ),
    entry: (scope, epoch, id, signal) =>
      this.request({ op: "adventure-entry", scope, epoch, id }, signal),
    decide: (decision, signal) =>
      this.request({ op: "adventure-decide", decision }, signal),
  };
}
