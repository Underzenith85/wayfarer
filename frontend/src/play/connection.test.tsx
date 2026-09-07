import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
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
  }) => (
    <section>
      <p>Signed in as {initialSession?.principal ?? "nobody"}</p>
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
      <button onClick={() => onOpen(disconnectedTransport)}>
        Open campaign
      </button>
    </section>
  ),
}));
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
        <Expire />
        <button onClick={onNewGame}>New game</button>
        <button onClick={onSwitchCampaign}>Switch campaign</button>
      </PlayProvider>
    </QueryClientProvider>
  ),
}));
it("replaces the setup shell with the game shell and keeps the session on return", async () => {
  const user = userEvent.setup();
  render(<ConnectedApp />);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  // Play is its own shell: no launcher and no setup panel above it.
  expect(screen.queryByRole("button", { name: "Continue game" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Open campaign" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Switch campaign" }));
  expect(screen.getByText("Signed in as alice")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Continue game" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await user.click(screen.getByRole("button", { name: "Open campaign" }));
  await user.click(screen.getByRole("button", { name: "Revoke access" }));
  await user.click(screen.getByRole("button", { name: "New game" }));
  expect(screen.getByText("Signed in as nobody")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New game" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
