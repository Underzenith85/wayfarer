import { useEffect, useState } from "react";
import { NetworkPlayTransport } from "../api/play-transport";
import {
  TacticalClient,
  TacticalError,
  type TacticalSnapshot,
  type TacticalCommand,
} from "../api/tactical";
import { Button } from "../components/ui/button";
import { usePlay } from "./use-play";

type Encounter = TacticalSnapshot["encounters"][number];
function HexMap({ encounter }: { encounter: Encounter }) {
  if (!encounter.cells.length) return null;
  const centers = encounter.cells.map((cell) => ({
    cell,
    x: 45 * (cell.position.q + cell.position.r / 2),
    y: 39 * cell.position.r,
  }));
  const left = Math.min(...centers.map((c) => c.x)) - 30;
  const top = Math.min(...centers.map((c) => c.y)) - 30;
  const width = Math.max(...centers.map((c) => c.x)) - left + 30;
  const height = Math.max(...centers.map((c) => c.y)) - top + 30;
  return (
    <svg
      className="tactical-map"
      role="img"
      aria-label="Visible tactical hex map; positions also listed below"
      viewBox={`${left} ${top} ${width} ${height}`}
    >
      {centers.map(({ cell, x, y }) => {
        const actors = encounter.actors.filter(
          (a) =>
            a.position.q === cell.position.q &&
            a.position.r === cell.position.r,
        );
        const points = Array.from({ length: 6 }, (_, index) => {
          const angle = ((index * 60 - 30) * Math.PI) / 180;
          return `${x + 25 * Math.cos(angle)},${y + 25 * Math.sin(angle)}`;
        }).join(" ");
        return (
          <g key={`${cell.position.q},${cell.position.r}`}>
            <polygon
              points={points}
              fill={
                cell.blocked
                  ? "#374151"
                  : actors.some((a) => a.controlled)
                    ? "#14532d"
                    : actors.length
                      ? "#7c2d12"
                      : "#182333"
              }
              stroke="#8796aa"
            />
            <text x={x} y={y + 4} fill="white" textAnchor="middle" fontSize="9">
              {actors.length
                ? actors.map((a) => a.name.slice(0, 2)).join("/")
                : `${cell.position.q},${cell.position.r}`}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function TacticalPanel({
  client,
  cid,
  actor,
  onChange,
}: {
  client: TacticalClient;
  cid: string;
  actor: string;
  onChange: () => Promise<void>;
}) {
  const [snapshot, setSnapshot] = useState<TacticalSnapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState<TacticalCommand | null>(null);
  const [scope] = useState(() => new AbortController());
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const value = await client.read(cid, actor, scope.signal);
        if (active)
          setSnapshot((old) =>
            !old || value.revision >= old.revision ? value : old,
          );
      } catch (e) {
        if (active)
          setError(
            e instanceof Error ? e.message : "Tactical connection interrupted",
          );
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [client, cid, actor, scope]);
  const run = async (command: TacticalCommand) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setRetry(command);
    try {
      const value = await client.execute(cid, command, scope.signal);
      setSnapshot((old) =>
        !old || value.revision >= old.revision ? value : old,
      );
      setRetry(null);
      await onChange();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Connection interrupted; retry the same action.",
      );
      if (e instanceof TacticalError) {
        setRetry(null);
        try {
          setSnapshot(await client.read(cid, actor, scope.signal));
        } catch {
          /* Keep the last authoritative view. */
        }
      }
    } finally {
      setBusy(false);
    }
  };
  if (!snapshot?.encounters.length)
    return error ? <p role="status">{error}</p> : null;
  return (
    <section className="scene-card tactical-panel" aria-label="Tactical combat">
      <h2>Tactical combat</h2>
      <p>
        Server-resolved actions · one hex = one yard · revision{" "}
        {snapshot.revision}
      </p>
      {error && <p role="alert">{error}</p>}
      <p role="status">
        {busy
          ? "Resolving action…"
          : retry
            ? "Response lost. Retry to recover the recorded result."
            : "Connected to the authoritative encounter."}
      </p>
      {retry && (
        <Button disabled={busy} onClick={() => void run(retry)}>
          Retry same action
        </Button>
      )}
      {snapshot.encounters.map((encounter) => (
        <div key={encounter.id}>
          <h3>
            Round {encounter.round} · {encounter.status}
          </h3>
          <p>
            {encounter.current_actor_id === actor
              ? "Your turn"
              : "Waiting for another combatant"}
          </p>
          {encounter.notice && <p role="status">{encounter.notice}</p>}
          <div className="tactical-layout">
            <HexMap encounter={encounter} />
            <div>
              <h4>Visible combatants</h4>
              <ul>
                {encounter.actors.map((a) => (
                  <li key={a.id}>
                    {a.name}
                    {a.controlled ? " (you)" : ""}: ({a.position.q},{" "}
                    {a.position.r}), facing {a.facing}, {a.posture}
                    {a.grappled ? ", grappled" : ""}
                    {a.pinned ? ", pinned" : ""}
                  </li>
                ))}
              </ul>
              {encounter.grips.length > 0 && (
                <>
                  <h4>Grapple control</h4>
                  <ul>
                    {encounter.grips.map((g) => (
                      <li key={g.id}>
                        {
                          encounter.actors.find((a) => a.id === g.holder_id)
                            ?.name
                        }{" "}
                        holds{" "}
                        {
                          encounter.actors.find((a) => a.id === g.target_id)
                            ?.name
                        }
                        : {g.location}, {g.hands.join(", ")}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>
          <h4>Legal choices</h4>
          <p>
            Every choice works with Tab and Enter. Facing 0 points right;
            directions increase clockwise.
          </p>
          <div className="tactical-actions">
            {encounter.choices.map((choice) => (
              <Button
                key={choice.command.id}
                disabled={busy || retry !== null}
                onClick={() => void run(choice.command)}
              >
                {choice.label}
              </Button>
            ))}
          </div>
          {!encounter.choices.length && (
            <p>No legal choices for this character at this stage.</p>
          )}
          <details>
            <summary>Resolution traces</summary>
            <ol>
              {encounter.traces.map((trace) => (
                <li key={trace.command_id}>
                  {trace.code.replaceAll("_", " ")} —{" "}
                  {trace.totals
                    .map(
                      (total, index) =>
                        `roll ${total} vs ${trace.targets[index]}`,
                    )
                    .join("; ")}
                  {trace.injury ? `; injury ${trace.injury}` : ""}
                  <small>{trace.source}</small>
                </li>
              ))}
            </ol>
          </details>
        </div>
      ))}
    </section>
  );
}

export function TacticalControls() {
  const { store, state } = usePlay();
  const cid = state.snapshot?.campaign.id;
  if (
    !(store.transport instanceof NetworkPlayTransport) ||
    !cid ||
    !state.actorId
  )
    return null;
  return (
    <TacticalPanel
      key={`${cid}:${state.actorId}`}
      client={store.transport.tactical}
      cid={cid}
      actor={state.actorId}
      onChange={() => store.refresh()}
    />
  );
}
