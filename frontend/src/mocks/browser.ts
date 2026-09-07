import { setupWorker } from "msw/browser";
import { createMockHandlers } from "./handlers";
import { fixtureCredential, type Scenario } from "./catalog";
import { NetworkPlayTransport } from "../api/play-transport";
/** Explicit developer-only sample mode; no backend or model credentials needed. */
export async function startMockPlay(
  scenario: Scenario,
  origin = location.origin,
) {
  const mock = createMockHandlers({ scenario, origin });
  const worker = setupWorker(...mock.handlers);
  await worker.start({
    quiet: true,
    onUnhandledRequest(request, print) {
      if (new URL(request.url).pathname.startsWith("/api/v1")) print.error();
    },
  });
  return new NetworkPlayTransport({
    origin,
    credential: fixtureCredential("player-1"),
    principalId: "player-1",
    sample: true,
  });
}
