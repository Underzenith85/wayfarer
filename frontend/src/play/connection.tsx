import { SetupLobby, type SetupSession } from "../setup/lobby";
import { useCallback, useState } from "react";
import { App } from "../app";
import { Button } from "../components/ui/button";
import type { PlayTransport } from "./transport";

/**
 * Setup and play are separate shells. Opening a campaign unmounts the launcher
 * and the setup panel, so the game shell starts at the top of every route; the
 * authenticated setup session is kept here so returning needs no second sign-in.
 */
export function ConnectedApp() {
  const [session, setSession] = useState<SetupSession>();
  const [transport, setTransport] = useState<PlayTransport>();
  const [opened, setOpened] = useState<string>();
  const [mode, setMode] = useState<"new" | "continue" | "join">("new");
  const clearSession = useCallback(() => setSession(undefined), []);
  const leave = useCallback((next: "new" | "continue") => {
    if (location.pathname !== "/") history.replaceState(null, "", "/");
    if (next === "new") setOpened(undefined);
    setMode(next);
    setTransport(undefined);
  }, []);
  if (transport)
    return (
      <App
        key={`${transport.principalId}:${transport.initialCampaignId}`}
        transport={transport}
        onSessionEnded={clearSession}
        onNewGame={() => leave("new")}
        onSwitchCampaign={() => leave("continue")}
      />
    );
  return (
    <>
      <header className="scene-card connection-form">
        <h1>Wayfarer</h1>
        <p>Start an adventure or return to your table.</p>
        <nav className="context-actions" aria-label="Game menu">
          {(["new", "continue", "join"] as const).map((value) => (
            <Button
              key={value}
              aria-pressed={mode === value}
              onClick={() => setMode(value)}
            >
              {value === "new"
                ? "New game"
                : value === "continue"
                  ? "Continue game"
                  : "Join game"}
            </Button>
          ))}
        </nav>
      </header>
      <SetupLobby
        mode={mode}
        initialSession={session}
        initialCampaignId={opened}
        onSession={setSession}
        onOpen={(next) => {
          if (location.pathname !== "/") history.replaceState(null, "", "/");
          setOpened(next.initialCampaignId);
          setTransport(next);
        }}
      />
    </>
  );
}
