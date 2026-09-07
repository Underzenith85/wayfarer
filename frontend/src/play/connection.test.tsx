import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
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
        <button onClick={() => onOpen(disconnectedTransport)}>
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
        <Expire />
      </PlayProvider>
    </QueryClientProvider>
  ),
}));
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
