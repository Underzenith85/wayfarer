import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConnectedApp } from "./connection";
import { PlayProvider } from "./context";
import { usePlay } from "./use-play";
import { disconnectedTransport, type PlayTransport } from "./transport";

vi.mock("../setup/lobby", () => ({
  SetupLobby: ({ onOpen }: { onOpen: (transport: PlayTransport) => void }) => {
    const [token, setToken] = useState("");
    const [campaign, setCampaign] = useState("");
    return (
      <section>
        <label>
          Access token
          <input value={token} onChange={(e) => setToken(e.target.value)} />
        </label>
        <label>
          Selected campaign
          <input
            value={campaign}
            onChange={(e) => setCampaign(e.target.value)}
          />
        </label>
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
function Expire() {
  const { store } = usePlay();
  return <button onClick={() => store.expire()}>Revoke access</button>;
}
vi.mock("../app", () => ({
  App: ({
    transport,
    onSessionEnded,
  }: {
    transport: PlayTransport;
    onSessionEnded?: () => void;
  }) => (
    <QueryClientProvider client={new QueryClient()}>
      <PlayProvider transport={transport} onSessionEnded={onSessionEnded}>
        <p>Open campaign: {transport.initialCampaignId ?? "none"}</p>
        <Expire />
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
            ? { principal_id: "alice", legacy_available: false }
            : { error: "Unknown access token" },
        ),
        { status: ok ? 200 : 401 },
      ),
    ),
  ) as typeof fetch;
}
it("preserves the authenticated lobby on return but clears it on revocation", async () => {
  const user = userEvent.setup();
  render(<ConnectedApp />);
  await user.type(screen.getByLabelText("Access token"), "alice-token");
  await user.type(screen.getByLabelText("Selected campaign"), "courier");
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  expect(screen.getByLabelText("Access token")).not.toBeVisible();
  await user.click(screen.getByRole("button", { name: "Continue game" }));
  expect(screen.getByLabelText("Access token")).toHaveValue("alice-token");
  expect(screen.getByLabelText("Selected campaign")).toHaveValue("courier");
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  await user.click(screen.getByRole("button", { name: "Revoke access" }));
  expect(screen.getByLabelText("Access token")).toHaveValue("");
  await user.click(screen.getByRole("button", { name: "Continue game" }));
  expect(screen.getByLabelText("Access token")).toHaveValue("");
  expect(screen.getByLabelText("Selected campaign")).toHaveValue("");
});
it("rehydrates the campaign and route on reload instead of asking to sign in again", async () => {
  sessionStorage.setItem(
    "wayfarer:session",
    JSON.stringify({
      credential: "alice-token",
      principalId: "alice",
      campaignId: "courier",
    }),
  );
  history.replaceState(null, "", "/c/courier/journal");
  authenticates();
  render(<ConnectedApp />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Restoring your session…",
  );
  expect(screen.queryByLabelText("Access token")).toBeNull();
  expect(await screen.findByText("Open campaign: courier")).toBeVisible();
  expect(location.pathname).toBe("/c/courier/journal");
});
it("prefers the campaign a pasted link names over the remembered one", async () => {
  sessionStorage.setItem(
    "wayfarer:session",
    JSON.stringify({
      credential: "alice-token",
      principalId: "alice",
      campaignId: "courier",
    }),
  );
  history.replaceState(null, "", "/c/beacon/character");
  authenticates();
  render(<ConnectedApp />);
  expect(await screen.findByText("Open campaign: beacon")).toBeVisible();
});
it("signs in again when the remembered credential no longer works", async () => {
  sessionStorage.setItem(
    "wayfarer:session",
    JSON.stringify({
      credential: "stale",
      principalId: "alice",
      campaignId: null,
    }),
  );
  authenticates(false);
  render(<ConnectedApp />);
  expect(await screen.findByLabelText("Access token")).toBeVisible();
  expect(sessionStorage.getItem("wayfarer:session")).toBeNull();
});
it("returns to the requested view after an unauthenticated visitor signs in", async () => {
  const user = userEvent.setup();
  history.replaceState(null, "", "/c/courier/inventory");
  render(<ConnectedApp />);
  await user.type(screen.getByLabelText("Access token"), "alice-token");
  await user.type(screen.getByLabelText("Selected campaign"), "beacon");
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  expect(location.pathname).toBe("/c/beacon/inventory");
});
