import { afterEach, beforeEach, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FixtureTransport } from "./fixtures";
import { PlayStore } from "./store";
import { Context } from "./use-play";
import { PlayWorkspace } from "./workspace";
import type { Snapshot } from "./transport";

/** A campaign whose player controls two characters in the same scene. */
class PartyTransport extends FixtureTransport {
  override async readSnapshot(id: string, signal: AbortSignal) {
    const s: Snapshot = await super.readSnapshot(id, signal);
    const second = { ...structuredClone(s.characters[0]!), id: "hero-9" };
    second.name = "Sera";
    s.characters.push(second);
    s.campaign.membership.actor_ids = ["hero-1", "hero-9"];
    s.scene.visible_actor_ids = ["hero-1", "hero-9"];
    return s;
  }
}
const stores: PlayStore[] = [];
async function mount(transport: FixtureTransport) {
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
const solo = () => mount(new FixtureTransport("resolve", 1));
// The composer renders a mic for a browser that has speech; a browser without
// it keeps the same field and Send, which src/voice covers separately.
beforeEach(() => {
  for (const [key, value] of [
    ["isSecureContext", true],
    ["SpeechRecognition", class {}],
    ["SpeechSynthesisUtterance", class {}],
    ["speechSynthesis", { speak() {}, cancel() {} }],
  ] as const)
    Object.defineProperty(window, key, { configurable: true, value });
});
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
  localStorage.clear();
});
const composer = () =>
  screen.getByLabelText("What do you do?").closest("form")!;

it("is one region holding the turn: suggestions, channel, mic and one primary action", async () => {
  await solo();
  const region = composer();
  // #197: the suggested actions are the composer's own first row, not a control
  // group a transcript away in the scene card.
  expect(
    within(region).getByRole("button", { name: "Inspect Locked door" }),
  ).toBeEnabled();
  // #196: the channel is a segmented control inside the composer; its group name
  // is read, not printed as a labelled region above the input.
  expect(within(region).getByRole("radio", { name: "Action" })).toBeChecked();
  expect(screen.getByText("Message channel")).toHaveClass("visually-hidden");
  // #195: voice is one mic control beside the field, and the privacy text is no
  // longer permanent body copy above it.
  expect(
    within(region).getByRole("button", { name: "Start voice input" }),
  ).toBeEnabled();
  expect(within(region).getByText(/Optional browser speech/)).toHaveClass(
    "visually-hidden",
  );
  // One primary action for the whole region.
  expect(
    within(region)
      .getAllByRole("button")
      .filter((b) => b.classList.contains("button-primary"))
      .map((b) => b.textContent),
  ).toEqual([expect.stringContaining("Send action")]);
});

it("keeps narration playback out of the composer, beside the transcript", async () => {
  await solo();
  const transcript = screen.getByRole("region", { name: "Play transcript" });
  const narration = within(transcript).getByRole("button", {
    name: "Narration",
  });
  expect(composer().contains(narration)).toBe(false);
  const controls = [
    "Mute narration",
    "Interrupt narration",
    "Replay latest narration",
  ];
  for (const name of controls)
    expect(screen.queryByRole("button", { name })).toBeNull();
  await userEvent.click(narration);
  const sheet = screen.getByRole("dialog");
  expect(
    within(sheet).getByLabelText("Speak new completed narration"),
  ).toBeInTheDocument();
  for (const name of controls)
    expect(within(sheet).getByRole("button", { name })).toBeInTheDocument();
});

it("discloses browser speech on first use of the mic, not above the input", async () => {
  await solo();
  await userEvent.click(
    screen.getByRole("button", { name: "Start voice input" }),
  );
  const notice = screen.getByRole("dialog", { name: "About browser speech" });
  expect(notice).toHaveTextContent(/No microphone starts until you choose/);
  await userEvent.click(
    within(notice).getByRole("button", { name: "Not now" }),
  );
  expect(
    screen.queryByRole("dialog", { name: "About browser speech" }),
  ).toBeNull();
  // The disclosure remains the mic's description once it is out of the way.
  expect(
    screen.getByRole("button", { name: "Start voice input" }),
  ).toHaveAccessibleDescription(/Optional browser speech/);
});

it("states a single controlled character instead of offering a choice", async () => {
  await solo();
  expect(screen.getByText(/^Acting as/)).toHaveTextContent("Acting as Mara");
  expect(screen.queryByRole("combobox")).toBeNull();
});

it("promotes acting as to a select once the player controls more than one", async () => {
  await mount(new PartyTransport("resolve", 1));
  const select = screen.getByRole("combobox", { name: /Acting as/ });
  expect(
    within(select)
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["Mara", "Sera"]);
});
