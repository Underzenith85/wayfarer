import { MultiplayerFixtureTransport } from "../multiplayer/fixture-transport";
import type { AdventurePort } from "./model";
export class AdventureFixtureTransport extends MultiplayerFixtureTransport {
  constructor(
    identity: "captive" | "rescuer",
    room: string,
    private closureJourney:
      "success" | "partial" | "failure" | "continue" | "archive" = "continue",
  ) {
    super(identity, room);
  }
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
    closure: (scope, epoch, signal) =>
      this.request(
        {
          op: "adventure-closure",
          scope,
          epoch,
          closureJourney: this.closureJourney,
        },
        signal,
      ),
    settle: (closure, signal) =>
      this.request({ op: "adventure-settle", closure }, signal),
  };
}
