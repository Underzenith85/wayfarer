import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import { usePlay } from "./use-play";
import { LiveTransport, type EngineProjection } from "./live";
export function LiveControls() {
  const { state, store } = usePlay();
  const [engine, setEngine] = useState<EngineProjection | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [rejoin, setRejoin] = useState("");
  const transport = store.transport;
  useEffect(() => {
    if (!(transport instanceof LiveTransport)) return;
    const controller = new AbortController();
    const update = async () => {
      try {
        const p = await transport.readEngine(controller.signal);
        if (!controller.signal.aborted) setEngine(p);
        await store.refresh();
      } catch (e) {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "Connection interrupted");
      }
    };
    void update();
    const timer = setInterval(() => void update(), 5000);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [transport, store]);
  if (!(transport instanceof LiveTransport) || !engine || !state.actorId)
    return null;
  const actor = state.actorId,
    scene = engine.scenes.find((s) => s.actor_id === actor),
    group = engine.subgroups.find((g) => g.actor_ids.includes(actor));
  const run = async (fields: Record<string, unknown>) => {
    if (engine.lifecycle && engine.lifecycle !== "active") {
      setError("Resume your campaign before acting.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await transport.command(actor, fields, new AbortController().signal);
      setEngine(await transport.readEngine(new AbortController().signal));
      await store.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="scene-card live-decisions" aria-label="Scene decisions">
      <h2>Scene decisions</h2>
      {error && <p role="alert">{error}</p>}
      <p role="status">
        {engine.objectives.outcome !== "ongoing"
          ? `Adventure ${engine.objectives.outcome}`
          : busy
            ? "Resolving your choice…"
            : group?.paused
              ? "Your group is paused"
              : "Your choices are available."}
      </p>
      <div className="context-actions">
        {scene?.exits.map((e) => (
          <Button
            key={e.id}
            disabled={busy}
            onClick={() => void run({ kind: "travel_scene", exit_id: e.id })}
          >
            Travel: {e.destination_id}
          </Button>
        ))}
        <Button
          disabled={busy}
          onClick={() => void run({ kind: "wait", ticks: 1 })}
        >
          Wait one tick
        </Button>
        <Button
          disabled={busy}
          onClick={() => void run({ kind: "split_party" })}
        >
          Split from group
        </Button>
        {group && (
          <Button
            disabled={busy}
            onClick={() =>
              void run({ kind: group.paused ? "resume_group" : "pause_group" })
            }
          >
            {group.paused ? "Resume group" : "Pause group"}
          </Button>
        )}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run({ kind: "rejoin_party", target_id: rejoin });
        }}
      >
        <label htmlFor="rejoin-group">Rejoin a known group</label>
        <input
          id="rejoin-group"
          value={rejoin}
          onChange={(e) => setRejoin(e.target.value)}
        />
        <Button disabled={busy || !rejoin}>Rejoin</Button>
      </form>
      {engine.encounters
        .filter((e) => e.status === "active")
        .map((e) => (
          <div key={e.id}>
            <h3>Encounter {e.id}</h3>
            {e.pending_defense?.defender_id === actor ? (
              <div className="context-actions">
                {e.pending_defense.allowed.map((d) => (
                  <Button
                    key={d}
                    disabled={busy}
                    onClick={() =>
                      void run({
                        kind: "choose_defense",
                        encounter_id: e.id,
                        defense: d,
                      })
                    }
                  >
                    {d}
                  </Button>
                ))}
              </div>
            ) : (
              <p>Current turn: {e.turn_order[e.turn_index]}</p>
            )}
          </div>
        ))}
      {engine.noncombat
        .filter((e) => e.actor_id === actor && e.status === "choice")
        .map((e) => (
          <div key={e.id}>
            <h3>Choose an approach</h3>
            {e.pending_choices.map((choice) => (
              <Button
                key={choice}
                disabled={busy}
                onClick={() =>
                  void run({
                    kind: "approach_noncombat",
                    encounter_id: e.id,
                    selection_id: choice,
                  })
                }
              >
                {choice}
              </Button>
            ))}
          </div>
        ))}
      {engine.recovery_choices
        .filter((o) => o.actor_id === actor)
        .map((o) => (
          <Button
            key={`${o.id}:${o.target_actor_id}`}
            disabled={busy}
            onClick={() =>
              void run({
                kind: "choose_recovery",
                rule_id: o.id,
                target_actor_id: o.target_actor_id,
              })
            }
          >
            {o.kind}: {o.id}
          </Button>
        ))}
      <details>
        <summary>Discoveries and commitments</summary>
        {engine.perspectives[actor]?.facts.map((f) => (
          <p key={f.id}>{f.value}</p>
        ))}
        {engine.perspectives[actor]?.commitments.map((c) => (
          <p key={c.id}>{c.description}</p>
        ))}
      </details>
    </section>
  );
}
