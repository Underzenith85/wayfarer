import { createRoot } from "react-dom/client";
import { App } from "./app";
import { disconnectedTransport, type PlayTransport } from "./play/transport";
import "./styles.css";
async function start() {
  let transport: PlayTransport = disconnectedTransport;
  if (import.meta.env.VITE_PLAY_FIXTURES === "true") {
    const { FixtureTransport } = await import("./play/fixtures");
    const params = new URLSearchParams(location.search);
    const inventory = params.get("inventory");
    const journey = params.get("journey");
    transport = new FixtureTransport(
      journey === "clarify" ||
        journey === "reject" ||
        journey === "retry" ||
        journey === "narration-failure" ||
        journey === "expired" ||
        journey === "stale"
        ? journey
        : "resolve",
    );
    if (inventory) {
      const { InventoryFixtureTransport, inventoryJourneys } =
        await import("./character/fixtures");
      const selected = inventoryJourneys.find((j) => j === inventory);
      if (selected) transport = new InventoryFixtureTransport(selected);
    }
  }
  createRoot(document.getElementById("root")!).render(
    <App transport={transport} />,
  );
}
void start();
