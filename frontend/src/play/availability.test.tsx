import { afterEach, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FixtureTransport } from "./fixtures";
import { PlayStore } from "./store";
import { Context } from "./use-play";
import { PlayWorkspace } from "./workspace";
import { ProviderBanner } from "../components/availability";
import {
  capabilityReason,
  providerBanner,
  providerReason,
} from "../presentation/availability";
import type { Snapshot } from "./transport";
/** A campaign the engine can still play: scene actions, but no AI provider. */
class NoProviderTransport extends FixtureTransport {
  constructor(private readonly capabilities: string[]) {
    super("resolve", 1);
  }
  override async readSnapshot(id: string, signal: AbortSignal) {
    const s: Snapshot = await super.readSnapshot(id, signal);
    s.campaign.capabilities = this.capabilities;
    s.scene.observations = [
      { id: "door-1", label: "Locked door", description: "Fresh scratches." },
      { id: "alley", label: "The Alley", description: "Known scene exit" },
    ];
    return s;
  }
}
const stores: PlayStore[] = [];
async function mount(capabilities: string[]) {
  const store = new PlayStore(
    new NoProviderTransport(capabilities),
    () => {},
    1,
  );
  stores.push(store);
  await store.select("campaign-1");
  store.chooseActor("hero-1");
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
  return store;
}
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
});
it("blocks the whole composer with the reason that actually applies", async () => {
  await mount(["actions.move", "actions.wait"]);
  expect(screen.getByLabelText("What do you do?")).toBeDisabled();
  expect(screen.getByRole("button", { name: /Send action/ })).toBeDisabled();
  expect(screen.getByText(providerReason.text)).toBeVisible();
  // The composer never repeats the shell's statement of the condition, and never
  // blames permission the signed-in player does hold.
  expect(screen.queryByText(providerBanner.summary)).toBeNull();
  expect(screen.queryByText(/campaign permission/)).toBeNull();
});
it("does not offer a scene action the engine would reject", async () => {
  await mount(["actions.move", "actions.wait"]);
  expect(
    screen.queryByRole("button", { name: "Inspect Locked door" }),
  ).toBeNull();
  // The observation stays readable as scene detail, with its own reason.
  expect(screen.getByText("Locked door")).toBeVisible();
  expect(screen.getByText(capabilityReason("inspect"))).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Travel to The Alley" }),
  ).toBeEnabled();
});
it("honours the targets a campaign names as inspectable", async () => {
  await mount([
    "actions.move",
    "actions.wait",
    "actions.inspect",
    "actions.inspect:alley",
  ]);
  expect(
    screen.queryByRole("button", { name: "Inspect Locked door" }),
  ).toBeNull();
  expect(screen.getByText(capabilityReason("inspect"))).toBeVisible();
});
it("states the condition once, with the affected capabilities behind a disclosure", () => {
  render(<ProviderBanner />);
  expect(screen.getByText(providerBanner.summary)).toBeVisible();
  expect(
    screen.getByText(providerBanner.disclosure).closest("details"),
  ).toBeInTheDocument();
  for (const item of providerBanner.affected)
    expect(screen.getByText(item)).toBeInTheDocument();
});
