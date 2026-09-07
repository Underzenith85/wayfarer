import { afterEach, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
const entries = () => [
  ...screen
    .getByRole("region", { name: "Play transcript" })
    .querySelectorAll<HTMLElement>(".transcript > li"),
];
it("shows error details and retry guidance beside a rejected action", async () => {
  const store = start(new FixtureTransport("reject", 1));
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "Search the dock");
  mount(store);
  const alert = await screen.findByRole("alert");
  fireEvent.click(within(alert).getByText("Error details"));
  expect(within(alert).getByText("Code: illegal_action")).toBeVisible();
  expect(within(alert).getByText(/Request ID:/)).toBeVisible();
  expect(
    within(alert).getByText("Resolve the issue before retrying."),
  ).toBeVisible();
});
it("shows what was submitted and when, across a reload (#202)", async () => {
  const transport = new FixtureTransport("resolve", 1);
  const store = start(transport);
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "I scan the harbor for the beacon keeper");
  await store.send("action", "I knock on the keeper’s door");
  // A fresh sign-in reads the same actions back from the service, which carries
  // no intent text of its own.
  await store.select("campaign-1");
  mount(store);
  const rows = entries();
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("I scan the harbor for the beacon keeper");
  expect(rows[1]).toHaveTextContent("I knock on the keeper’s door");
  // Two different actions read as two different entries.
  expect(rows[0]!.textContent).not.toBe(rows[1]!.textContent);
  for (const row of rows)
    expect(row.querySelector("time")).toHaveAttribute(
      "datetime",
      "2026-09-06T22:00:00Z",
    );
  expect(screen.queryByText("Previously submitted action")).toBeNull();
});
it("says so rather than inventing text it never kept (#202)", async () => {
  const transport = new FixtureTransport("resolve", 1);
  const store = start(transport);
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "I scan the harbor for the beacon keeper");
  // Another device, or a browser whose storage was cleared, keeps no record.
  localStorage.clear();
  await store.select("campaign-1");
  mount(store);
  const [row] = entries();
  expect(row).toHaveTextContent(
    "This turn's text was not kept on this device.",
  );
  expect(row!.querySelector("time")).toBeInTheDocument();
});
it("prints the location name once when the projection echoes it (#203)", async () => {
  class EchoTransport extends FixtureTransport {
    override async readSnapshot(id: string, signal: AbortSignal) {
      const s: Snapshot = await super.readSnapshot(id, signal);
      s.scene.title = "Stormbound Harbor";
      s.scene.description = "Stormbound Harbor";
      return s;
    }
  }
  const store = start(new EchoTransport("resolve", 1));
  await store.select("campaign-1");
  mount(store);
  const card = screen
    .getByRole("heading", { name: "Stormbound Harbor" })
    .closest("section")!;
  expect(within(card).getAllByText("Stormbound Harbor")).toHaveLength(1);
  expect(card.querySelector(".scene-description")).toBeNull();
});
it("keeps a description the projection actually wrote (#203)", async () => {
  const store = start();
  await store.select("campaign-1");
  mount(store);
  expect(screen.getByText(/Rain taps the narrow window/)).toBeVisible();
});
