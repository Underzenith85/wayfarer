import { createRoot } from "react-dom/client";
import { App } from "./app";
import { disconnectedTransport, type PlayTransport } from "./play/transport";
import "./styles.css";
async function start() {
  let transport: PlayTransport = disconnectedTransport;
  if (import.meta.env.VITE_PLAY_FIXTURES === "true") {
    const { FixtureTransport } = await import("./play/fixtures");
    const journey = new URLSearchParams(location.search).get("journey");
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
  }
  createRoot(document.getElementById("root")!).render(
    <App transport={transport} />,
  );
}
void start();
