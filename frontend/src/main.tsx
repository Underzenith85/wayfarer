import { PwaStatus } from "./pwa";
import { createRoot } from "react-dom/client";
import { ConnectedApp } from "./play/connection";
import { App } from "./app";
import { disconnectedTransport, type PlayTransport } from "./play/transport";
import "./styles.css";
async function start() {
  let transport: PlayTransport = disconnectedTransport;
  if (import.meta.env.VITE_PLAY_FIXTURES === "true") {
    const params = new URLSearchParams(location.search);
    const identity = params.get("multiplayer");
    const inventory = params.get("inventory");
    if (params.get("onboarding") === "true") {
      const { onboardingTransport } =
        await import("./onboarding/fixture-transport");
      transport = onboardingTransport(
        params.get("identity") === "guest" ? "guest" : "host",
        params.get("room") ?? "onboarding-demo",
      );
    } else if (params.get("adventure") === "true") {
      const { AdventureFixtureTransport } =
        await import("./adventure/fixture-transport");
      transport = new AdventureFixtureTransport(
        identity === "rescuer" ? "rescuer" : "captive",
        params.get("room") ?? "adventure-demo",
        (
          ["success", "partial", "failure", "continue", "archive"] as const
        ).find((journey) => journey === params.get("closure")) ?? "continue",
      );
    } else if (identity === "captive" || identity === "rescuer") {
      const { MultiplayerFixtureTransport } =
        await import("./multiplayer/fixture-transport");
      transport = new MultiplayerFixtureTransport(
        identity,
        params.get("room") ?? "demo",
      );
    } else if (inventory) {
      const { InventoryFixtureTransport, inventoryJourneys } =
        await import("./character/fixtures");
      const selected = inventoryJourneys.find((j) => j === inventory);
      if (selected) transport = new InventoryFixtureTransport(selected);
    }
    if (transport === disconnectedTransport) {
      const { startMockPlay } = await import("./mocks/browser");
      const { isScenario } = await import("./mocks/catalog");
      const journey = params.get("journey");
      transport = await startMockPlay(
        isScenario(journey) ? journey : "resolve",
      );
    }
  }
  createRoot(document.getElementById("root")!).render(
    <>
      <PwaStatus />
      {transport === disconnectedTransport ? (
        <ConnectedApp />
      ) : (
        <App transport={transport} />
      )}
    </>,
  );
}
void start();
