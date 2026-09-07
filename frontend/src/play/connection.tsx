import { SetupLobby } from "../setup/lobby";
import { useCallback, useEffect, useState } from "react";
import { App } from "../app";
import { Button } from "../components/ui/button";
import { NetworkPlayTransport } from "../api/play-transport";
import { SetupClient } from "../setup/client";
import { LiveTransport } from "./live";
import { pagePath, parsePath } from "../routes";
import {
  forgetSession,
  readSession,
  rememberCampaign,
  rememberRequestedPath,
  takeRequestedPath,
} from "./session";
import type { PlayTransport } from "./transport";

interface Restored {
  credential: string;
  transport: PlayTransport | null;
}
/**
 * Rebuilds the tab's session without asking for the access token again. The URL
 * wins over the remembered campaign, so a pasted link opens the view it names.
 * With no campaign to open, the lobby simply opens already signed in.
 */
async function restore(): Promise<Restored | null> {
  const saved = readSession();
  if (!saved) return null;
  const auth = await new SetupClient(saved.credential).request<{
    principal_id: string;
    legacy_available: boolean;
  }>("/session");
  const campaignId =
    parsePath(location.pathname)?.campaignId ?? saved.campaignId;
  if (!campaignId) return { credential: saved.credential, transport: null };
  rememberCampaign(campaignId);
  return {
    credential: saved.credential,
    transport: auth.legacy_available
      ? new LiveTransport(auth.principal_id, campaignId, saved.credential)
      : new NetworkPlayTransport({
          origin: location.origin,
          credential: saved.credential,
          principalId: auth.principal_id,
          initialCampaignId: campaignId,
        }),
  };
}

export function ConnectedApp() {
  const [session, setSession] = useState(0);
  const [restored, setRestored] = useState<string>();
  const clearSession = useCallback(() => {
    forgetSession();
    setRestored(undefined);
    setSession((value) => value + 1);
  }, []);
  const [transport, setTransport] = useState<PlayTransport>();
  const [mode, setMode] = useState<"new" | "continue" | "join">("new");
  const [restoring, setRestoring] = useState(() => readSession() !== null);
  useEffect(() => {
    if (!restoring) return;
    let active = true;
    void restore()
      .then((next) => {
        if (!active || !next) return;
        setRestored(next.credential);
        if (next.transport) setTransport(next.transport);
      })
      .catch(() => {
        // A revoked or expired credential simply falls back to signing in.
        forgetSession();
      })
      .finally(() => {
        if (active) setRestoring(false);
      });
    return () => {
      active = false;
    };
    // Rehydration runs once per tab, on boot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Hold the requested view so signing in returns to it instead of the default.
  useEffect(() => {
    if (restoring || transport) return;
    if (location.pathname !== "/")
      rememberRequestedPath(location.pathname + location.search);
  }, [restoring, transport]);
  // A reload restores the session in the background; showing the sign-in form
  // here would ask for an access token the tab already holds.
  if (restoring)
    return (
      <section className="scene-card connection-form">
        <h1>Wayfarer</h1>
        <div role="status" aria-busy="true">
          <h2>Restoring your session…</h2>
          <div className="skeleton" />
        </div>
      </section>
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
          restored={restored}
          onOpen={(next) => {
            const requested = takeRequestedPath();
            const segment = requested
              ? (parsePath(new URL(requested, location.origin).pathname)
                  ?.segment ?? "")
              : "";
            const target = pagePath(next.initialCampaignId ?? null, segment);
            if (location.pathname !== target)
              history.replaceState(null, "", target);
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
