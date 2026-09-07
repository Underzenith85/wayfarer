import { createRoot } from "react-dom/client";
import { ConnectedApp } from "./play/connection";
import { App } from "./app";
import { disconnectedTransport, type PlayTransport } from "./play/transport";
import "./styles.css";
async function start() {
  let transport: PlayTransport = disconnectedTransport;
  if (import.meta.env.VITE_PLAY_FIXTURES === "true") {
    const params = new URLSearchParams(location.search);
    const inventory = params.get("inventory");
    if (inventory) {
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
    transport === disconnectedTransport ? (
      <ConnectedApp />
    ) : (
      <App transport={transport} />
    ),
  );
}
void start();
