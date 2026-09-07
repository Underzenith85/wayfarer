import { SetupLobby } from "../setup/lobby";
import { useState } from "react";
import { App } from "../app";
import { Button } from "../components/ui/button";
import { LiveTransport } from "./live";
import type { PlayTransport } from "./transport";
export function ConnectedApp() {
  const [transport, setTransport] = useState<PlayTransport>(),
    [campaign, setCampaign] = useState(""),
    [principal, setPrincipal] = useState(""),
    [token, setToken] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <>
      {transport ? (
        <div className="offline-banner">
          Connected as {transport.principalId}{" "}
          <Button onClick={() => setTransport(undefined)}>Disconnect</Button>
        </div>
      ) : (
        <form
          className="scene-card connection-form"
          aria-label="Campaign connection"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            const next = new LiveTransport(principal, campaign, token);
            void next
              .listCampaigns(new AbortController().signal)
              .then(() => {
                setTransport(next);
                setToken("");
              })
              .catch((e: unknown) =>
                setError(e instanceof Error ? e.message : "Connection failed"),
              )
              .finally(() => setBusy(false));
          }}
        >
          <h2>Connect to your campaign</h2>
          <label htmlFor="campaign-id">Campaign ID</label>
          <input
            id="campaign-id"
            required
            value={campaign}
            onChange={(e) => setCampaign(e.target.value)}
          />
          <label htmlFor="principal-id">Player ID</label>
          <input
            id="principal-id"
            required
            value={principal}
            onChange={(e) => setPrincipal(e.target.value)}
          />
          <label htmlFor="campaign-token">Access token</label>
          <input
            id="campaign-token"
            type="password"
            required
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <Button disabled={busy}>{busy ? "Connecting…" : "Connect"}</Button>
          {error && <p role="alert">{error}</p>}
        </form>
      )}
      <details open={!transport}>
        <summary>Campaign setup and lifecycle</summary>
        <SetupLobby onOpen={setTransport} />
      </details>
      <App
        key={
          transport
            ? `${transport.principalId}:${transport.initialCampaignId}`
            : "disconnected"
        }
        {...(transport ? { transport } : {})}
      />
    </>
  );
}
