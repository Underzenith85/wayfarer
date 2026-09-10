import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConnectedApp } from "./connection";
import { PlayProvider } from "./context";
import { usePlay } from "./use-play";
import { disconnectedTransport, type PlayTransport } from "./transport";
import type { SetupSession } from "../setup/lobby";

vi.mock("../setup/lobby", () => ({
  SetupLobby: ({
    onOpen,
    initialSession,
    onSession,
  }: {
    onOpen: (transport: PlayTransport) => void;
    initialSession?: SetupSession;
    onSession?: (value: SetupSession | undefined) => void;
  }) => {
    const [campaign, setCampaign] = useState("");
    return (
      <section>
        <p>Signed in as {initialSession?.principal ?? "nobody"}</p>
        <label>
          Selected campaign
          <input
            value={campaign}
            onChange={(e) => setCampaign(e.target.value)}
          />
        </label>
        <button
          onClick={() =>
            onSession?.({
              token: "alice-token",
              principal: "alice",
              generationAvailable: false,
              legacyAvailable: false,
            })
          }
        >
          Sign in
        </button>
        <button
          onClick={() =>
            onOpen(
              campaign
                ? { ...disconnectedTransport, initialCampaignId: campaign }
                : disconnectedTransport,
            )
          }
        >
          Open campaign
        </button>
      </section>
    );
  },
}));
/** The one tab a lobby-mode tab bar reports as selected. */
function selected() {
  return screen
    .getAllByRole("tab")
    .find((tab) => tab.getAttribute("aria-selected") === "true")?.textContent;
}
function Expire() {
  const { store } = usePlay();
  return <button onClick={() => store.expire()}>Revoke access</button>;
}
vi.mock("../app", () => ({
  App: ({
    transport,
    onSessionEnded,
    onNewGame,
    onSwitchCampaign,
  }: {
    transport: PlayTransport;
    onSessionEnded?: () => void;
    onNewGame?: () => void;
    onSwitchCampaign?: () => void;
  }) => (
    <QueryClientProvider client={new QueryClient()}>
      <PlayProvider transport={transport} onSessionEnded={onSessionEnded}>
        <p>Open campaign: {transport.initialCampaignId ?? "none"}</p>
        <Expire />
        <button onClick={onNewGame}>New game</button>
        <button onClick={onSwitchCampaign}>Switch campaign</button>
      </PlayProvider>
    </QueryClientProvider>
  ),
}));
const original = globalThis.fetch;
beforeEach(() => {
  sessionStorage.clear();
  history.replaceState(null, "", "/");
});
afterEach(() => {
  globalThis.fetch = original;
});
function authenticates(ok = true) {
  globalThis.fetch = vi.fn(() =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          ok
            ? {
                principal_id: "alice",
                generation_available: false,
                legacy_available: false,
              }
            : { error: "Unknown access token" },
        ),
        { status: ok ? 200 : 401 },
      ),
    ),
  ) as typeof fetch;
}
function remembered(campaignId: string | null) {
  sessionStorage.setItem(
    "wayfarer:session",
    JSON.stringify({
      credential: "alice-token",
      principalId: "alice",
      campaignId,
    }),
  );
}
it("replaces the setup shell with the game shell and keeps the session on return", async () => {
  const user = userEvent.setup();
  render(<ConnectedApp />);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  // Play is its own shell: no launcher and no setup panel above it.
  expect(screen.queryByRole("tab", { name: "Join game" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Open campaign" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Switch campaign" }));
  expect(screen.getByText("Signed in as alice")).toBeInTheDocument();
  expect(selected()).toBe("Join game");
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  await user.click(screen.getByRole("button", { name: "Revoke access" }));
  await user.click(screen.getByRole("button", { name: "New game" }));
  expect(screen.getByText("Signed in as nobody")).toBeInTheDocument();
  expect(selected()).toBe("Start game");
});

it("gives setup the same landmarks the play shell has (#257)", async () => {
  render(<ConnectedApp />);
  // The skip link has somewhere real to go: setup renders a main region, not a
  // bare panel div, so a screen reader can jump the header and the tab bar.
  const main = document.querySelector("main");
  expect(main).not.toBeNull();
  const skip = screen.getByRole("link", { name: "Skip to content" });
  expect(skip).toHaveAttribute("href", `#${main!.id}`);
  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "Setup" })).toBeInTheDocument();
  expect(screen.getByRole("main")).toBe(main);
  expect(within(main!).getByRole("tabpanel")).toBeInTheDocument();
  // Neither shell carries a footer landmark since the scene footer copy was
  // removed, so parity here is that setup does not invent one of its own.
  expect(screen.queryByRole("contentinfo")).toBeNull();
});

it("selects a lobby mode from the tab itself, by pointer and by arrow key", async () => {
  const user = userEvent.setup();
  render(<ConnectedApp />);
  // The mode is readable from the control, and the panel it reveals is named
  // by that tab instead of repeating its label as a heading (#200).
  expect(selected()).toBe("Start game");
  const panel = screen.getByRole("tabpanel");
  expect(panel).toHaveAccessibleName("Start game");
  expect(
    within(panel).queryByRole("heading", { name: "Start game" }),
  ).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Join game" }));
  expect(selected()).toBe("Join game");
  expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Join game");
  // A tab bar is walked with the arrow keys; only the selected tab is a stop.
  screen.getByRole("tab", { name: "Join game" }).focus();
  await user.keyboard("{ArrowRight}");
  expect(selected()).toBe("Create scenario");
  expect(screen.getByRole("tab", { name: "Create scenario" })).toHaveFocus();
  await user.keyboard("{ArrowRight}");
  expect(selected()).toBe("Start game");
  await user.keyboard("{End}");
  expect(selected()).toBe("Create scenario");
  expect(screen.getByRole("tab", { name: "Join game" })).toHaveAttribute(
    "tabindex",
    "-1",
  );
});
it("rehydrates the campaign and route on reload instead of asking to sign in again", async () => {
  remembered("courier");
  history.replaceState(null, "", "/c/courier/journal");
  authenticates();
  render(<ConnectedApp />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Restoring your session…",
  );
  expect(await screen.findByText("Open campaign: courier")).toBeVisible();
  expect(location.pathname).toBe("/c/courier/journal");
});
it("prefers the campaign a pasted link names over the remembered one", async () => {
  remembered("courier");
  history.replaceState(null, "", "/c/beacon/character");
  authenticates();
  render(<ConnectedApp />);
  expect(await screen.findByText("Open campaign: beacon")).toBeVisible();
});
it("resumes setup already signed in when no campaign was open", async () => {
  remembered(null);
  authenticates();
  render(<ConnectedApp />);
  expect(await screen.findByText("Signed in as alice")).toBeVisible();
});
it("signs in again when the remembered credential no longer works", async () => {
  remembered(null);
  authenticates(false);
  render(<ConnectedApp />);
  expect(await screen.findByText("Signed in as nobody")).toBeVisible();
  expect(sessionStorage.getItem("wayfarer:session")).toBeNull();
});
it("returns to the requested view after an unauthenticated visitor signs in", async () => {
  const user = userEvent.setup();
  history.replaceState(null, "", "/c/courier/inventory");
  render(<ConnectedApp />);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.type(screen.getByLabelText("Selected campaign"), "beacon");
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  expect(location.pathname).toBe("/c/beacon/inventory");
});
it("stops reopening a campaign the player has left", async () => {
  const user = userEvent.setup();
  render(<ConnectedApp />);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.type(screen.getByLabelText("Selected campaign"), "courier");
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  expect(location.pathname).toBe("/c/courier");
  await user.click(screen.getByRole("button", { name: "Switch campaign" }));
  expect(location.pathname).toBe("/");
  expect(
    JSON.parse(sessionStorage.getItem("wayfarer:session")!) as {
      campaignId: string | null;
    },
  ).toMatchObject({ credential: "alice-token", campaignId: null });
});
