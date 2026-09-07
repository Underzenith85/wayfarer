import { AdventureAuthority } from "./adventure-authority";
import type {
  ClosureCommand,
  DecisionCommand,
  JournalKind,
} from "../src/adventure/model";
import type { Plugin } from "vite";
import { MultiplayerAuthority, type Identity } from "./multiplayer-authority";
import {
  TransportError,
  type ClarifyAction,
  type SubmitAction,
} from "../src/play/transport";
import type { Scope, TableCommand } from "../src/multiplayer/model";

export function multiplayerFixtures(): Plugin {
  const adventures = new Map<string, AdventureAuthority>();
  const rooms = new Map<string, MultiplayerAuthority>();
  return {
    name: "multiplayer-fixtures",
    apply: "serve",
    configureServer(server) {
      if (process.env.VITE_PLAY_FIXTURES !== "true") return;
      server.middlewares.use("/__fixtures/multiplayer", async (req, res) => {
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Content-Type", "application/json");
        try {
          const identity = req.headers["x-mock-identity"];
          const room = req.headers["x-mock-room"];
          if (
            (identity !== "captive" && identity !== "rescuer") ||
            typeof room !== "string" ||
            !/^[a-zA-Z0-9_-]{1,80}$/.test(room)
          )
            throw new TransportError(
              "unauthenticated",
              "Choose a mock identity and room.",
            );
          if (req.method !== "POST")
            throw new TransportError(
              "not_found",
              "Fixture operation unavailable.",
            );
          let raw = "";
          for await (const chunk of req) {
            raw += String(chunk);
            if (raw.length > 32000)
              throw new Error("Fixture request too large");
          }
          const input = JSON.parse(raw) as {
            op: string;
            query: string;
            kind: JournalKind | "all";
            id: string;
            since: string | null;
            decision: DecisionCommand;
            closure: ClosureCommand;
            closureJourney:
              "success" | "partial" | "failure" | "continue" | "archive";
            actorId?: string;
            scope: Scope;
            cursor: string;
            epoch: string;
            command: SubmitAction & ClarifyAction & TableCommand;
            actionId: string;
            scenario: "rescue" | "revoke" | "reassign" | "missed";
          };
          if (!rooms.has(room)) {
            if (rooms.size >= 100) {
              const oldest = rooms.keys().next().value!;
              rooms.delete(oldest);
              adventures.delete(oldest);
            }
            rooms.set(room, new MultiplayerAuthority(room));
            adventures.set(room, new AdventureAuthority(rooms.get(room)!));
          }
          const authority = rooms.get(room)!;
          const who: Identity = identity;
          let value: unknown;
          switch (input.op) {
            case "adventure-overview":
              value = adventures
                .get(room)!
                .overview(who, input.scope, input.epoch, input.since);
              break;
            case "adventure-search":
              value = adventures
                .get(room)!
                .search(who, input.scope, input.epoch, input.query, input.kind);
              break;
            case "adventure-entry":
              value = adventures
                .get(room)!
                .entry(who, input.scope, input.epoch, input.id);
              break;
            case "adventure-decide":
              adventures.get(room)!.decide(who, input.decision);
              value = null;
              break;
            case "adventure-closure":
              value = adventures
                .get(room)!
                .closure(who, input.scope, input.epoch, input.closureJourney);
              break;
            case "adventure-settle":
              value = adventures.get(room)!.settle(who, input.closure);
              break;
            case "campaigns":
              value = [authority.read(who, null).snapshot.campaign];
              break;
            case "read":
              value = authority.read(who, input.actorId ?? null);
              break;
            case "events":
              value = authority.events(
                who,
                input.scope,
                input.cursor,
                input.epoch,
              );
              break;
            case "submit":
              value = authority.submit(who, input.command);
              break;
            case "clarify":
              value = authority.clarify(
                who,
                input.actorId!,
                input.actionId,
                input.command,
              );
              break;
            case "action":
              value = authority
                .read(who, input.actorId ?? null)
                .actions.find((a) => a.id === input.actionId);
              if (!value)
                throw new TransportError("not_found", "Action unavailable.");
              break;
            case "command":
              authority.command(who, input.command);
              value = null;
              break;
            case "scenario":
              authority.scenario(input.scenario, who);
              value = null;
              break;
            default:
              throw new TransportError(
                "not_found",
                "Fixture operation unavailable.",
              );
          }
          res.end(JSON.stringify(value));
        } catch (error) {
          res.statusCode =
            error instanceof TransportError && error.code === "forbidden"
              ? 403
              : 400;
          res.end(
            JSON.stringify({
              code:
                error instanceof TransportError ? error.code : "illegal_action",
              message:
                error instanceof Error
                  ? error.message
                  : "Invalid fixture request",
            }),
          );
        }
      });
    },
  };
}
