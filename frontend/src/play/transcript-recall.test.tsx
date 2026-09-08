import { afterEach, expect, it, vi } from "vitest";
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
it("reports a refused attempt at the composer, not in the story (#298)", async () => {
  const store = start(new FixtureTransport("reject", 1));
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "Search the dock");
  mount(store);
  const alert = await screen.findByRole("alert");
  // The transcript is the story, and nothing about this attempt is in it.
  expect(entries()).toHaveLength(0);
  expect(
    screen.getByRole("region", { name: "Play transcript" }).contains(alert),
  ).toBe(false);
  // The diagnostic detail is still one disclosure away, where the player is.
  fireEvent.click(within(alert).getByText("Error details"));
  expect(within(alert).getByText("Code: illegal_action")).toBeVisible();
  expect(within(alert).getByText(/Request ID:/)).toBeVisible();
  expect(
    within(alert).getByText("Resolve the issue before retrying."),
  ).toBeVisible();
  // And it leaves when the player is done with it, for good.
  fireEvent.click(within(alert).getByRole("button", { name: "Dismiss" }));
  expect(screen.queryByRole("alert")).toBeNull();
  await store.select("campaign-1");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(entries()).toHaveLength(0);
});
it("reads a refusal from an earlier session as history, not an alarm (#298)", async () => {
  const transport = new FixtureTransport("reject", 1);
  const store = start(transport);
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "Search the dock");
  // A fresh sign-in finds the same refused action and says nothing new about it.
  const resumed = start(transport);
  await resumed.select("campaign-1");
  resumed.chooseActor("hero-1");
  mount(resumed);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(entries()).toHaveLength(0);
  expect(screen.getByText(/^Failed attempts \(1\)/)).toBeVisible();
});
it("orders the transcript by service time after a reload (#295)", async () => {
  const transport = new FixtureTransport("resolve", 1);
  const times = ["2026-09-06T22:00:09Z", "2026-09-06T22:00:03Z"];
  const stamped = new Map<string, string>();
  const listActions = transport.listActions.bind(transport);
  const getAction = transport.getAction.bind(transport);
  vi.spyOn(transport, "getAction").mockImplementation(async (c, id, signal) => {
    stamped.set(id, stamped.get(id) ?? times[stamped.size] ?? times[0]!);
    return {
      ...(await getAction(c, id, signal)),
      created_at: stamped.get(id)!,
    };
  });
  vi.spyOn(transport, "listActions").mockImplementation(async (c, signal) =>
    // Identifiers sort one way and the clock another; the clock decides.
    (await listActions(c, signal))
      .map((a) => ({ ...a, created_at: stamped.get(a.id) ?? a.created_at }))
      .reverse(),
  );
  const store = start(transport);
  await store.select("campaign-1");
  store.chooseActor("hero-1");
  await store.send("action", "Taken first, recorded later");
  await store.send("action", "Taken second, recorded earlier");
  await store.select("campaign-1");
  mount(store);
  expect(entries().map((row) => row.querySelector("time")?.dateTime)).toEqual([
    "2026-09-06T22:00:03Z",
    "2026-09-06T22:00:09Z",
  ]);
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
