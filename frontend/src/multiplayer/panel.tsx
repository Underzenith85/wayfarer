import { useState } from "react";
import { Button } from "../components/ui/button";
import { usePlay } from "../play/use-play";
import type { GroupKind } from "./model";

export function ConnectionStatus() {
  const { state, store } = usePlay();
  if (state.expired || state.connection === "online") return null;
  return (
    <section className="connection-status" aria-label="Scene connection">
      <p role="status">
        {state.connection === "offline"
          ? "Disconnected · showing the last received scene. Drafts are saved; actions are paused."
          : "Reconciling your authorized scene…"}
      </p>
      {state.lastSynchronized && (
        <p>
          Last synchronized:{" "}
          <time dateTime={state.lastSynchronized}>
            {new Date(state.lastSynchronized).toLocaleString()}
          </time>
        </p>
      )}
      {state.error && <p role="alert">{state.error}</p>}
      <Button
        variant="outline"
        disabled={state.loading}
        onClick={() => void store.reconnect()}
      >
        Reconnect scene
      </Button>
    </section>
  );
}
export function MultiplayerPanel() {
  const { state, store } = usePlay();
  const view = state.multiplayer;
  const [kind, setKind] = useState<GroupKind>("split");
  const [target, setTarget] = useState("");
  const [showOoc, setShowOoc] = useState(false);
  if (!view) return null;
  const blocked =
    state.connection !== "online" ||
    state.busy ||
    !!state.retry ||
    !!state.tableRetry ||
    state.needsRefresh;
  const destinations = view.destinations.filter((d) => d.kinds.includes(kind));
  const destination = destinations.some((d) => d.id === target)
    ? target
    : (destinations[0]?.id ?? "");
  const me = view.presence.find((p) => p.id === store.transport.principalId);
  return (
    <section
      className="multiplayer-panel"
      aria-label="Scene and companions"
      data-checkpoint={view.checkpoint.cursor}
    >
      <div className="multiplayer-heading">
        <h2>Scene & companions</h2>
        <span className="sample-label">
          {state.connection === "online" ? "Connected" : "Last received status"}
        </span>
      </div>
      <label>
        Controlled character
        <select
          aria-label="Controlled character"
          value={state.actorId ?? ""}
          disabled={blocked}
          onChange={(e) => store.chooseActor(e.target.value)}
        >
          {view.controlledActors.map((actor) => (
            <option key={actor.id} value={actor.id}>
              {actor.name}
            </option>
          ))}
        </select>
      </label>
      <p>
        Only permitted scene membership and presence are shown. Other groups
        make their own decisions.
      </p>
      <ul className="presence-list">
        {view.presence.map((p) => (
          <li key={p.id}>
            <strong>{p.name}</strong>
            <span>
              {p.status} · {p.ready ? "Ready" : "Not ready"}
            </span>
          </li>
        ))}
      </ul>
      <Button
        variant="outline"
        disabled={blocked || !me}
        onClick={() =>
          void store.tableCommand({ kind: "ready", ready: !me?.ready })
        }
      >
        {me?.ready ? "Mark not ready" : "Mark ready"}
      </Button>
      <details className="group-controls">
        <summary>Move with your group</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (destination)
              void store.tableCommand({ kind, destinationId: destination });
          }}
        >
          <label>
            Group operation
            <select
              aria-label="Group operation"
              value={kind}
              disabled={blocked}
              onChange={(e) => {
                setKind(e.target.value as GroupKind);
                setTarget("");
              }}
            >
              <option value="split">Split</option>
              <option value="transfer">Transfer</option>
              <option value="rejoin">Rejoin</option>
            </select>
          </label>
          <label>
            Destination
            <select
              aria-label="Destination"
              value={destination}
              disabled={blocked || !destinations.length}
              onChange={(e) => setTarget(e.target.value)}
            >
              {!destinations.length && (
                <option value="">No permitted destination</option>
              )}
              {destinations.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
          <p>
            This moves only your selected character. The server checks travel,
            pending decisions, and membership before committing.
          </p>
          <Button disabled={blocked || !destination}>Confirm {kind}</Button>
        </form>
      </details>
      {state.tableRetry && (
        <div role="alert">
          <p>
            The table request may have been accepted. Retry its original
            identity to retrieve the outcome.
          </p>
          <Button
            disabled={state.busy || state.connection !== "online"}
            onClick={() => void store.retryTable()}
          >
            Retry table request
          </Button>
        </div>
      )}
      {view.ooc.enabled && (
        <div className="ooc-panel">
          <label>
            <input
              type="checkbox"
              checked={showOoc}
              onChange={(e) => setShowOoc(e.target.checked)}
            />{" "}
            Show out-of-character table chat
          </label>
          {showOoc && (
            <section aria-label="Out-of-character table chat">
              <p>
                Shared with the table. Messages do not change character
                knowledge or game facts; share discoveries only when you intend
                to.
              </p>
              <ul>
                {view.ooc.messages.map((m) => (
                  <li key={m.id}>
                    <strong>{m.author}: </strong>
                    {m.text}
                  </li>
                ))}
              </ul>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void store.tableCommand({
                    kind: "ooc",
                    text: state.drafts.ooc,
                  });
                }}
              >
                <label>
                  Table message
                  <textarea
                    aria-label="Table message"
                    value={state.drafts.ooc}
                    maxLength={2000}
                    onChange={(e) => store.saveDraft("ooc", e.target.value)}
                  />
                </label>
                <Button disabled={blocked || !state.drafts.ooc.trim()}>
                  Send table message
                </Button>
              </form>
            </section>
          )}
        </div>
      )}
    </section>
  );
}
