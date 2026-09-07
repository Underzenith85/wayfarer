import { afterEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FixtureTransport } from "./fixtures";
import { PlayStore } from "./store";
import { Context } from "./use-play";
import { PlayWorkspace } from "./workspace";
import type { Snapshot } from "./transport";
/** A campaign with no provider: free text is offered but cannot be submitted. */
class NoTextTransport extends FixtureTransport {
  override async readSnapshot(id: string, signal: AbortSignal) {
    const s: Snapshot = await super.readSnapshot(id, signal);
    s.campaign.capabilities = ["actions.move", "actions.wait"];
    return s;
  }
}
const stores: PlayStore[] = [];
async function mount(
  transport: FixtureTransport = new FixtureTransport("resolve", 1),
) {
  const store = new PlayStore(transport, () => {}, 1);
  stores.push(store);
  await store.select("campaign-1");
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
  localStorage.clear();
  vi.useRealTimers();
});
const composer = () =>
  screen.getByLabelText("What do you do?").closest("form")!;
it("never accepts input it cannot submit (#201)", async () => {
  await mount(new NoTextTransport("resolve", 1));
  expect(screen.getByLabelText("What do you do?")).toBeDisabled();
  expect(screen.getByRole("button", { name: /Send action/ })).toBeDisabled();
  // A field that takes nothing has nothing to discard either.
  expect(screen.queryByRole("button", { name: "Discard draft" })).toBeNull();
});
it("labels a restored draft with its age and discards it in one interaction (#201)", async () => {
  const store = await mount();
  const days = 3 * 24 * 60 * 60 * 1000;
  vi.setSystemTime(new Date(Date.now() - days));
  store.saveDraft("action", "I scan the harbor for the beacon keeper");
  vi.useRealTimers();
  // What a fresh sign-in does: the same scope, read back from this device.
  await store.select("campaign-1");
  const region = composer();
  expect(screen.getByLabelText("What do you do?")).toHaveValue(
    "I scan the harbor for the beacon keeper",
  );
  expect(
    within(region).getByText(/Draft saved on this device 3 days ago/),
  ).toBeVisible();
  await userEvent.click(
    within(region).getByRole("button", { name: "Discard draft" }),
  );
  expect(screen.getByLabelText("What do you do?")).toHaveValue("");
  expect(store.readDraft("action").text).toBe("");
  expect(
    Object.keys(localStorage).filter((k) => k.startsWith("wayfarer:draft")),
  ).toEqual([]);
  expect(screen.queryByRole("button", { name: "Discard draft" })).toBeNull();
});
it("states that nothing is held when the field is empty (#201)", async () => {
  await mount();
  expect(
    within(composer()).getByText(/Nothing saved on this device/),
  ).toBeVisible();
});
