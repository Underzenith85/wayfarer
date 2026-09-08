import { afterEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// The workspace is mounted without the app router; only its own copy is under test.
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
  useLocation: ({ select }: { select: (l: { pathname: string }) => string }) =>
    select({ pathname: "/" }),
  useNavigate: () => () => {},
}));
import { FixtureTransport } from "./fixtures";
import { PlayStore } from "./store";
import { Context } from "./use-play";
import { PlayWorkspace } from "./workspace";
import type { Snapshot } from "./transport";
const stores: PlayStore[] = [];
function mount(store: PlayStore) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Context.Provider value={store}>
        <PlayWorkspace />
      </Context.Provider>
    </QueryClientProvider>,
  );
}
function start(transport = new FixtureTransport("resolve", 1)) {
  const store = new PlayStore(transport, () => {}, 1);
  stores.push(store);
  return store;
}
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
  localStorage.clear();
});
const entry = () =>
  screen
    .getByRole("region", { name: "Play transcript" })
    .querySelector<HTMLElement>(".transcript > li")!;
it("renders the game master's prose as the body of a committed turn (#294)", async () => {
  const store = start();
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "I dress the wound and listen at the door");
  mount(store);
  const row = entry();
  const prose = row.querySelector<HTMLElement>(".gm-message")!;
  expect(prose).toHaveTextContent(/You draw the clean linen tight/);
  // The narrative answer comes before the engine's account of the same turn,
  // which used to be the entire visible result.
  expect(
    prose.compareDocumentPosition(
      row.querySelector<HTMLElement>(".committed-result")!,
    ) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});
it("keeps digests and internal ids out of rolls and consequences (#296)", async () => {
  const store = start();
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "I dress the wound");
  mount(store);
  const panel = entry().querySelector<HTMLElement>(
    ".committed-result details",
  )!;
  const shown = within(panel);
  expect(shown.getByText(/^Updated /)).toHaveTextContent(
    "Updated your character, your inventory and this scene.",
  );
  expect(shown.getByText(/Game time:/)).toBeInTheDocument();
  // The versions and the identifiers they name are engine bookkeeping, so they
  // sit behind the same disclosure every other digest in the app uses.
  const technical = panel.querySelector<HTMLElement>(".technical-details")!;
  expect(technical).toBeInTheDocument();
  const visible = panel.textContent!.replace(technical.textContent!, "");
  for (const hidden of ["h2", "i2", "s2", "hero-1", "cellar-1"])
    expect(visible).not.toContain(hidden);
});
it("states a finished adventure as an outcome with a way onward (#297)", async () => {
  class FinishedTransport extends FixtureTransport {
    override async readSnapshot(id: string, signal: AbortSignal) {
      const s: Snapshot = await super.readSnapshot(id, signal);
      s.campaign.status = "completed";
      return s;
    }
  }
  const store = start(new FinishedTransport("resolve", 1));
  await store.select("campaign-1");
  mount(store);
  const panel = within(
    screen
      .getByRole("heading", { name: "Adventure complete" })
      .closest("section")!,
  );
  // The outcome is named, not merely the fact that no turn will be accepted.
  expect(panel.getByText(/Find the courier before dawn/)).toBeVisible();
  expect(panel.getByText(/ticks of game time/)).toBeVisible();
  expect(
    panel.getByRole("link", { name: "Start another adventure" }),
  ).toBeVisible();
  expect(
    panel.getByRole("link", { name: "Review this session" }),
  ).toBeVisible();
  // Winning is not a permission failure, and the composer is not the answer.
  expect(screen.queryByLabelText("What do you do?")).toBeNull();
  expect(screen.queryByText(/accepts no actions/)).toBeNull();
});
it("states a blocking lifecycle condition exactly once (#297)", async () => {
  class PausedTransport extends FixtureTransport {
    override async readSnapshot(id: string, signal: AbortSignal) {
      const s: Snapshot = await super.readSnapshot(id, signal);
      s.campaign.status = "paused";
      s.scene.observations = [
        { id: "alley", label: "The Alley", description: "Known scene exit" },
      ];
      s.campaign.capabilities = [...s.campaign.capabilities, "actions.move"];
      return s;
    }
  }
  const store = start(new PausedTransport("resolve", 1));
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  mount(store);
  const reason = "This campaign is paused, so it accepts no actions.";
  expect(store.sendBlockReason("text")).toBe(reason);
  // The suggestions above the field and the composer below it shared one
  // condition and each printed it; it belongs once, beside the input.
  expect(screen.getAllByText(reason)).toHaveLength(1);
});
