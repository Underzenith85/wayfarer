import { afterEach, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FixtureTransport, fixtureSnapshot } from "../play/fixtures";
import { enrichSnapshot } from "./sample-data";
import { PlayStore } from "../play/store";
import { Context } from "../play/use-play";
import { CharacterPage, InventoryPage } from "./pages";
import { CharacterSummary } from "../play/workspace";
import type { Snapshot } from "../play/transport";
const digest =
  "4f2d9fddce6b6585234d2921bb3f4acba25722b0d9fe507ed5a1b98dad6b9205";
/** What the v1 projection actually sends: engine keys, enum values and digests. */
class EngineProjectionTransport extends FixtureTransport {
  override async readSnapshot(id: string, signal: AbortSignal) {
    const s: Snapshot = await super.readSnapshot(id, signal);
    const character = s.characters[0]!;
    character.version = digest;
    character.attributes = [
      { id: "attribute:dx", label: "attribute:dx", value: 12 },
      { id: "attribute:ht", label: "attribute:ht", value: 10 },
      { id: "attribute:iq", label: "attribute:iq", value: 11 },
      { id: "attribute:st", label: "attribute:st", value: 10 },
    ];
    character.conditions = [
      { id: "stunned", label: "stunned", description: "stunned" },
    ];
    const inventory = s.inventories[0]!;
    inventory.version = digest;
    inventory.encumbrance = "Not supplied by the current engine projection";
    return s;
  }
}
const stores: PlayStore[] = [];
async function mount(component: React.ReactNode) {
  const store = new PlayStore(
    new EngineProjectionTransport("resolve", 1),
    () => {},
    1,
  );
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
describe("engine identifiers stay out of the sheet", () => {
  it("renders attributes as ST, DX, IQ, HT in canonical order", async () => {
    await mount(<CharacterPage />);
    const attributes = screen
      .getByRole("heading", { name: "Attributes" })
      .closest("section")!;
    expect(
      within(attributes)
        .getAllByRole("term")
        .map((term) => term.textContent),
    ).toEqual(["ST", "DX", "IQ", "HT"]);
    expect(within(attributes).getByLabelText("Strength")).toHaveAttribute(
      "title",
      "Strength",
    );
  });
  it("names a condition projected as a bare enum value", async () => {
    await mount(<CharacterPage />);
    expect(screen.getByText("Stunned")).toBeVisible();
    expect(screen.queryByText("stunned")).toBeNull();
  });
  it("keeps the version digest out of the default view", async () => {
    await mount(<CharacterPage />);
    for (const shown of screen.getAllByText(digest, { exact: false }))
      expect(shown).not.toBeVisible();
    await userEvent.click(screen.getByText("Technical details"));
    expect(screen.getByText(`Character version ${digest}`)).toBeVisible();
  });
  it("says encumbrance is not reported instead of echoing the placeholder", async () => {
    await mount(<InventoryPage />);
    expect(screen.getByText("Encumbrance not reported")).toBeVisible();
    expect(screen.queryByText("Not supplied", { exact: false })).toBeNull();
  });
  it("keeps both digests out of the at-a-glance rail", async () => {
    await mount(<CharacterSummary />);
    for (const shown of screen.getAllByText(digest, { exact: false }))
      expect(shown).not.toBeVisible();
    await userEvent.click(screen.getByText("Technical details"));
    expect(screen.getByText(`Character version ${digest}`)).toBeVisible();
    expect(screen.getByText(`Inventory version ${digest}`)).toBeVisible();
  });
});
