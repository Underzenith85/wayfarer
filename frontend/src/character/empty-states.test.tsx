import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FixtureTransport, fixtureSnapshot } from "../play/fixtures";
import { enrichSnapshot } from "./sample-data";
import { PlayStore } from "../play/store";
import { Context } from "../play/use-play";
import { CharacterPage, InventoryPage } from "./pages";
import { Journal } from "../play/workspace";
import { TransportError, type Snapshot } from "../play/transport";
// These pages are mounted without the app router; only their own copy is under test.
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
  useLocation: ({ select }: { select: (l: { pathname: string }) => string }) =>
    select({ pathname: "/" }),
  useNavigate: () => () => {},
}));
/** The live projection carries no detail bundles and can report empty collections. */
class SparseTransport extends FixtureTransport {
  override async readSnapshot(id: string, signal: AbortSignal) {
    const s: Snapshot = await super.readSnapshot(id, signal);
    delete s.characterDetails;
    delete s.inventoryDetails;
    for (const character of s.characters) {
      character.skills = [];
      character.defenses = [];
      character.movement = [];
      character.conditions = [];
    }
    if (s.session) s.session.summary = "";
    return s;
  }
}
/** A campaign that fails to load once, then loads. */
class FailingTransport extends FixtureTransport {
  attempts = 0;
  override async readSnapshot(id: string, signal: AbortSignal) {
    if (this.attempts++ === 0)
      throw new TransportError(
        "service_unavailable",
        "A player campaign connection is required",
      );
    return super.readSnapshot(id, signal);
  }
}
const stores: PlayStore[] = [];
async function mount(transport: FixtureTransport, component: React.ReactNode) {
  const store = new PlayStore(transport, () => {}, 1);
  stores.push(store);
  await store.select("campaign-1");
  const actor = enrichSnapshot(fixtureSnapshot("campaign-1")).characters[0]!.id;
  store.chooseActor(actor);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={client}>
      <Context.Provider value={store}>{component}</Context.Provider>
    </QueryClientProvider>,
  );
  return store;
}
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
});
/** Nothing a player reads may name the transport or the engine's verification state. */
function expectPlayerVoice() {
  for (const phrase of [
    /this connection/i,
    /projection/i,
    /unverified capabilit/i,
  ])
    expect(screen.queryByText(phrase)).toBeNull();
}
describe("empty regions speak in the game's voice", () => {
  it("names what belongs in each empty statistic table", async () => {
    await mount(new SparseTransport("resolve", 1), <CharacterPage />);
    const skills = screen
      .getByRole("heading", { name: "Skills" })
      .closest("section")!;
    expect(
      within(skills).getByText(/has not learned any skills yet/),
    ).toBeVisible();
    expect(screen.queryByText("No skills reported.")).toBeNull();
    expect(screen.getByText(/has no defenses to roll yet/)).toBeVisible();
    expect(screen.getByText(/has no movement rates yet/)).toBeVisible();
    expectPlayerVoice();
  });
  it("explains missing derived effects and points without naming the transport", async () => {
    await mount(new SparseTransport("resolve", 1), <CharacterPage />);
    const effects = screen
      .getByRole("heading", { name: "Derived effects" })
      .closest("section")!;
    expect(
      within(effects).getByText(/not part of this game yet/),
    ).toBeVisible();
    const points = screen
      .getByRole("heading", { name: "Points & advancement" })
      .closest("section")!;
    expect(
      within(points).getByText(/Point totals and advancement are not part/),
    ).toBeVisible();
    expectPlayerVoice();
  });
  it("describes an inventory without currency detail in player terms", async () => {
    await mount(new SparseTransport("resolve", 1), <InventoryPage />);
    expect(screen.getByText(/Coin and custody are not part/)).toBeVisible();
    expectPlayerVoice();
  });
  it("says what will fill an empty session recap", async () => {
    await mount(new SparseTransport("resolve", 1), <Journal />);
    expect(
      screen.getByText(/Your story so far will be summarised here/),
    ).toBeVisible();
    expect(screen.queryByText("No session recap is available yet.")).toBeNull();
  });
});
describe("a failed load is not an empty collection", () => {
  it("alerts, keeps the reason technical and reloads on retry", async () => {
    const transport = new FailingTransport("resolve", 1);
    const store = await mount(transport, <CharacterPage />);
    expect(screen.queryByText("No character selected")).toBeNull();
    const alert = screen.getByRole("alert");
    expect(
      within(alert).getByRole("heading", {
        name: "This character sheet did not load",
      }),
    ).toBeVisible();
    expect(
      within(alert).getByText("A player campaign connection is required", {
        exact: false,
      }),
    ).not.toBeVisible();
    await userEvent.click(
      within(alert).getByRole("button", { name: "Load the sheet again" }),
    );
    const actor = enrichSnapshot(fixtureSnapshot("campaign-1")).characters[0]!;
    store.chooseActor(actor.id);
    expect(
      await screen.findByRole("heading", { name: actor.name }),
    ).toBeVisible();
    expect(transport.attempts).toBe(2);
  });
});
