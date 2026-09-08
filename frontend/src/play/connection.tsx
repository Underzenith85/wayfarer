import { SetupLobby, type SetupMode, type SetupSession } from "../setup/lobby";
import { useCallback, useEffect, useState } from "react";
import { App } from "../app";
import { NetworkPlayTransport } from "../api/play-transport";
import { SetupClient } from "../setup/client";
import { LiveTransport } from "./live";
import { pagePath, parsePath } from "../routes";
import {
  forgetSession,
  readSession,
  rememberCampaign,
  rememberPrincipal,
  rememberRequestedPath,
  takeRequestedPath,
} from "./session";
import type { PlayTransport } from "./transport";

/**
 * Setup modes are tabs, not three primary actions: one is always selected, and
 * the panel below is the one the selected tab names (#200).
 */
const modes = [
  { value: "new", label: "New game" },
  { value: "continue", label: "Continue game" },
  { value: "join", label: "Join game" },
  // Scenario authoring is a library the setup flow draws on, not a step of it
  // (#261): it sits beside the game modes rather than inside one.
  { value: "scenarios", label: "Scenarios" },
] as const;
type Mode = SetupMode;
const panelId = "game-mode-panel";
const tabId = (value: Mode) => `game-mode-${value}`;
/** Arrow keys walk the tab bar and Home/End reach its ends, as tabs do. */
const step = (index: number, key: string) =>
  key === "ArrowRight"
    ? (index + 1) % modes.length
    : key === "ArrowLeft"
      ? (index - 1 + modes.length) % modes.length
      : key === "Home"
        ? 0
        : key === "End"
          ? modes.length - 1
          : undefined;

/**
 * Rebuilds the tab's session without asking for the access token again. The URL
 * wins over the remembered campaign, so a pasted link opens the view it names;
 * with no campaign to open, setup simply starts already signed in.
 */
async function restore(): Promise<{
  session: SetupSession;
  transport: PlayTransport | null;
} | null> {
  const saved = readSession();
  if (!saved) return null;
  const auth = await new SetupClient(saved.credential).request<{
    principal_id: string;
    generation_available: boolean;
    legacy_available: boolean;
  }>("/session");
  const session: SetupSession = {
    token: saved.credential,
    principal: auth.principal_id,
    generationAvailable: auth.generation_available,
    legacyAvailable: auth.legacy_available,
  };
  const campaignId =
    parsePath(location.pathname)?.campaignId ?? saved.campaignId;
  if (!campaignId) return { session, transport: null };
  rememberCampaign(campaignId);
  return {
    session,
    transport: session.legacyAvailable
      ? new LiveTransport(session.principal, campaignId, session.token)
      : new NetworkPlayTransport({
          origin: location.origin,
          credential: session.token,
          principalId: session.principal,
          initialCampaignId: campaignId,
        }),
  };
}

/**
 * Setup and play are separate shells. Opening a campaign unmounts the launcher
 * and the setup panel, so the game shell starts at the top of every route; the
 * authenticated setup session is kept here, and in this tab's session storage,
 * so returning or reloading needs no second sign-in.
 */
export function ConnectedApp() {
  const [session, setSession] = useState<SetupSession>();
  const [transport, setTransport] = useState<PlayTransport>();
  const [opened, setOpened] = useState<string>();
  const [mode, setMode] = useState<Mode>("new");
  const [restoring, setRestoring] = useState(() => readSession() !== null);
  const remember = useCallback((value: SetupSession | undefined) => {
    setSession(value);
    if (value) rememberPrincipal(value.token, value.principal);
    else forgetSession();
  }, []);
  const clearSession = useCallback(() => {
    forgetSession();
    setSession(undefined);
  }, []);
  const leave = useCallback((next: Extract<Mode, "new" | "continue">) => {
    // Leaving play drops the campaign from the URL and from what a reload opens.
    if (location.pathname !== "/") history.replaceState(null, "", "/");
    rememberCampaign(null);
    if (next === "new") setOpened(undefined);
    setMode(next);
    setTransport(undefined);
  }, []);
  useEffect(() => {
    if (!restoring) return;
    let active = true;
    void restore()
      .then((next) => {
        if (!active || !next) return;
        setSession(next.session);
        if (next.transport) {
          setOpened(next.transport.initialCampaignId);
          setTransport(next.transport);
        }
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
      {/* Setup is long and form-heavy, so it carries the same landmarks the
          play shell does: a skip link that lands somewhere real, a header, a
          navigation region, one main region and a footer (#257). */}
      <a className="skip-link" href="#setup-main">
        Skip to content
      </a>
      <header className="scene-card connection-form lobby-header">
        <h1>Wayfarer</h1>
        <p>Start an adventure or return to your table.</p>
        <nav aria-label="Setup">
          <div className="mode-tabs" role="tablist" aria-label="Game menu">
            {modes.map(({ value, label }, index) => (
              <button
                key={value}
                type="button"
                role="tab"
                id={tabId(value)}
                className="mode-tab"
                aria-selected={mode === value}
                aria-controls={panelId}
                tabIndex={mode === value ? 0 : -1}
                onKeyDown={(event) => {
                  const target = step(index, event.key);
                  if (target === undefined) return;
                  event.preventDefault();
                  setMode(modes[target]!.value);
                  document.getElementById(tabId(modes[target]!.value))?.focus();
                }}
                onClick={() => setMode(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </nav>
      </header>
      <main id="setup-main" tabIndex={-1}>
        <div id={panelId} role="tabpanel" aria-labelledby={tabId(mode)}>
          <SetupLobby
            mode={mode}
            onMode={setMode}
            initialSession={session}
            initialCampaignId={opened}
            onSession={remember}
            onOpen={(next) => {
              const requested = takeRequestedPath();
              const segment = requested
                ? (parsePath(new URL(requested, location.origin).pathname)
                    ?.segment ?? "")
                : "";
              const target = pagePath(next.initialCampaignId ?? null, segment);
              if (location.pathname !== target)
                history.replaceState(null, "", target);
              if (next.initialCampaignId)
                rememberCampaign(next.initialCampaignId);
              setOpened(next.initialCampaignId);
              setTransport(next);
            }}
          />
        </div>
        <footer className="scene-footer">
          The engine keeps the facts. The story brings them to life.
        </footer>
      </main>
    </>
  );
}
