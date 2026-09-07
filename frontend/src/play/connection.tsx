import { SetupLobby } from "../setup/lobby";
import { useCallback, useState } from "react";
import { App } from "../app";
import { Button } from "../components/ui/button";
import type { PlayTransport } from "./transport";

export function ConnectedApp() {
  const [session, setSession] = useState(0);
  const clearSession = useCallback(() => setSession((value) => value + 1), []);
  const [transport, setTransport] = useState<PlayTransport>();
  const [mode, setMode] = useState<"new" | "continue" | "join">("new");
  return (
    <>
      <header className="scene-card connection-form">
        <h1>Wayfarer</h1>
        <p>Start an adventure or return to your table.</p>
        <nav className="context-actions" aria-label="Game menu">
          {(["new", "continue", "join"] as const).map((value) => (
            <Button
              key={value}
              aria-pressed={!transport && mode === value}
              onClick={() => {
                setTransport(undefined);
                setMode(value);
              }}
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
      <div hidden={!!transport}>
        <SetupLobby
          key={session}
          mode={mode}
          onOpen={(next) => {
            if (location.pathname !== "/") history.replaceState(null, "", "/");
            setTransport(next);
          }}
        />
      </div>
      {transport && (
        <App
          key={`${transport.principalId}:${transport.initialCampaignId}`}
          transport={transport}
          onSessionEnded={clearSession}
        />
      )}
    </>
  );
}
