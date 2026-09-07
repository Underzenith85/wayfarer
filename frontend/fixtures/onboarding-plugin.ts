import type { Plugin } from "vite";
import { OnboardingAuthority } from "./onboarding-authority";
import { TransportError } from "../src/play/transport";
import type { Command } from "../src/onboarding/model";
export function onboardingFixtures(): Plugin {
  const rooms = new Map<string, OnboardingAuthority>();
  return {
    name: "onboarding-fixtures",
    apply: "serve",
    configureServer(server) {
      if (process.env.VITE_PLAY_FIXTURES !== "true") return;
      server.middlewares.use("/__fixtures/onboarding", async (req, res) => {
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Content-Type", "application/json");
        try {
          const who = req.headers["x-mock-identity"],
            room = req.headers["x-mock-room"];
          if (
            (who !== "host" && who !== "guest") ||
            typeof room !== "string" ||
            !/^[a-zA-Z0-9_-]{1,80}$/.test(room)
          )
            throw new TransportError(
              "unauthenticated",
              "Choose a mock identity and room.",
            );
          if (req.method !== "POST") throw new Error("POST required.");
          let raw = "";
          for await (const chunk of req) {
            raw += String(chunk);
            if (raw.length > 32000) throw new Error("Request too large.");
          }
          const input = JSON.parse(raw) as { op: string; command: Command };
          if (!rooms.has(room)) {
            if (rooms.size >= 100) rooms.delete(rooms.keys().next().value!);
            rooms.set(room, new OnboardingAuthority());
          }
          const a = rooms.get(room)!;
          let value: unknown;
          switch (input.op) {
            case "read":
              value = a.read(who);
              break;
            case "command":
              value = a.command(who, input.command);
              break;
            case "snapshot":
              value = a.snapshot(who);
              break;
            default:
              throw new Error("Unknown fixture operation.");
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
                error instanceof Error ? error.message : "Invalid request.",
            }),
          );
        }
      });
    },
  };
}
