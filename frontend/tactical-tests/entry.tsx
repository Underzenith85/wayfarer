// Test-only harness mounts the production panel and generated HTTP client unchanged.
import { createRoot } from "react-dom/client";
import { TacticalPanel } from "../src/play/tactical";
import { TacticalClient } from "../src/api/tactical";
import "../src/styles.css";
const params = new URLSearchParams(location.search);
const principal = params.get("principal") ?? "alice";
createRoot(document.getElementById("root")!).render(
  <TacticalPanel
    client={new TacticalClient("", `${principal}-token`)}
    cid={params.get("cid")!}
    actor={principal === "alice" ? "a" : "b"}
    onChange={async () => {}}
  />,
);
