import { createRoot } from "react-dom/client";
import { App } from "./app";
import { disconnectedTransport, type PlayTransport } from "./play/transport";
import "./styles.css";
async function start() {
  let transport: PlayTransport = disconnectedTransport;
  if (import.meta.env.VITE_PLAY_FIXTURES === "true") {
    const { startMockPlay } = await import("./mocks/browser");
    const { isScenario } = await import("./mocks/catalog");
    const journey = new URLSearchParams(location.search).get("journey");
    transport = await startMockPlay(isScenario(journey) ? journey : "resolve");
  }
  createRoot(document.getElementById("root")!).render(
    <App transport={transport} />,
  );
}
void start();
