import { useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePlay } from "../play/use-play";
import type { AdventurePort, DecisionCommand, JournalKind } from "./model";
import type { Scope } from "../multiplayer/model";
import { Button } from "../components/ui/button";
import { ScopedLink } from "../scoped-link";

function Boundary({
  children,
}: {
  children: (port: AdventurePort, scope: Scope, epoch: string) => ReactNode;
}) {
  const { state, store } = usePlay();
  if (state.expired) return <p role="alert">Perspective access unavailable.</p>;
  if (!state.snapshot || !state.actorId)
    return <p>Select a campaign and character to view discoveries.</p>;
  if (!store.transport.adventure || !state.multiplayer)
    return (
      <p>
        Encounter and discovery details are not available from this connection.
      </p>
    );
  if (state.loading || state.connection !== "online")
    return (
      <p role="status">Reconnect to load current encounter and discoveries.</p>
    );
  return children(
    store.transport.adventure,
    state.multiplayer.scope,
    state.multiplayer.checkpoint.epoch,
  );
}
function key(scope: Scope, epoch: string) {
  return `${scope.campaignId}:${scope.sceneId}:${scope.actorId}:${epoch}`;
}
export function EncounterPanel() {
  const { store } = usePlay();
  if (!store.transport.adventure) return null;
  return (
    <Boundary>
      {(port, scope, epoch) => (
        <EncounterControls
          key={key(scope, epoch)}
          port={port}
          scope={scope}
          epoch={epoch}
        />
      )}
    </Boundary>
  );
}
export function SessionClosure() {
  return (
    <Boundary>
      {(port, scope, epoch) => (
        <ClosureContents
          key={key(scope, epoch)}
          port={port}
          scope={scope}
          epoch={epoch}
        />
      )}
    </Boundary>
  );
}
function ClosureContents({
  port,
  scope,
  epoch,
}: {
  port: AdventurePort;
  scope: Scope;
  epoch: string;
}) {
  const { store } = usePlay();
  const client = useQueryClient();
  const [selections, setSelections] = useState<Record<string, string>>({});
  const [retry, setRetry] = useState<import("./model").ClosureCommand | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const result = useQuery({
    queryKey: [
      "adventure",
      store.transport.principalId,
      key(scope, epoch),
      "closure",
    ],
    queryFn: ({ signal }) => port.closure(scope, epoch, signal),
  });
  async function settle(command: import("./model").ClosureCommand) {
    setError(null);
    try {
      await port.settle(command, new AbortController().signal);
      setRetry(null);
      await client.invalidateQueries({
        queryKey: ["adventure", store.transport.principalId],
      });
    } catch (cause) {
      setRetry(command);
      setError(
        cause instanceof Error ? cause.message : "Settlement unavailable.",
      );
    }
  }
  if (result.isPending) return <p role="status">Loading session closure…</p>;
  if (result.error || !result.data)
    return (
      <p role="alert">
        Session closure unavailable.{" "}
        <Button onClick={() => void result.refetch()}>Retry</Button>
      </p>
    );
  const view = result.data;
  return (
    <div className="closure-stack">
      <section className="scene-card closure-hero">
        <div className="card-top">
          <span className={`outcome outcome-${view.outcome}`}>
            {view.outcome} outcome
          </span>
          <span className="eyebrow">
            Session {view.session.status} · Campaign {view.campaign.status}
          </span>
        </div>
        <h2>
          {view.session.status === "paused"
            ? "The story pauses here"
            : "Adventure complete"}
        </h2>
        <p className="scene-description">{view.session.summary}</p>
        {view.epilogue.map((line) => (
          <p key={line}>{line}</p>
        ))}
        {view.campaign.canContinue && (
          <p>
            <strong>
              This adventure’s outcome is not the end of the campaign.
            </strong>
          </p>
        )}
      </section>
      <section className="scene-card closure-section">
        <h2>Known objective outcomes</h2>
        {view.objectives.map((objective) => (
          <article key={objective.id} className="closure-row">
            <span className={`outcome outcome-${objective.outcome}`}>
              {objective.outcome}
            </span>
            <div>
              <h3>{objective.title}</h3>
              <p>{objective.detail}</p>
            </div>
          </article>
        ))}
      </section>
      <section className="scene-card closure-section">
        <h2>Rewards and lasting consequences</h2>
        {view.rewards.length ? (
          view.rewards.map((reward) => (
            <p key={reward.id}>
              <strong>{reward.label}</strong> · {reward.detail}
            </p>
          ))
        ) : (
          <p>No rewards were authorized for this outcome.</p>
        )}
        {view.consequences.map((item) => (
          <p key={item.id}>
            <strong>{item.kind}</strong> · {item.detail}
          </p>
        ))}
        <p className="sample-note">
          Outcomes and rewards come from the campaign authority. This screen
          cannot create them.
        </p>
      </section>
      <section className="scene-card closure-section">
        <h2>Downtime and advancement</h2>
        {view.advancement.map((item) => (
          <fieldset
            key={item.id}
            disabled={view.settlement.status === "settled"}
          >
            <legend>{item.label}</legend>
            {item.options.map((option) => (
              <label className="advancement-option" key={option.id}>
                <input
                  type="radio"
                  name={item.id}
                  checked={
                    (view.settlement.status === "settled"
                      ? item.selectedId
                      : selections[item.id]) === option.id
                  }
                  onChange={() =>
                    setSelections({ ...selections, [item.id]: option.id })
                  }
                />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.detail}</small>
                </span>
              </label>
            ))}
          </fieldset>
        ))}
        {view.settlement.status === "settled" ? (
          <p role="status">
            <strong>Rewards settled.</strong> This settlement cannot be claimed
            again.
          </p>
        ) : (
          <Button
            disabled={view.advancement.some((item) => !selections[item.id])}
            onClick={() =>
              void settle({
                commandId: crypto.randomUUID(),
                scope,
                epoch,
                version: view.version,
                settlementId: view.settlement.id,
                selections: view.advancement.map((item) => ({
                  advancementId: item.id,
                  optionId: selections[item.id]!,
                })),
              })
            }
          >
            Settle rewards and choices
          </Button>
        )}
        {retry && (
          <Button variant="outline" onClick={() => void settle(retry)}>
            Retry recorded settlement
          </Button>
        )}
        {error && <p role="alert">{error}</p>}
      </section>
      {view.nextAdventure && (
        <section className="scene-card closure-section">
          <span className="eyebrow">Next adventure preview</span>
          <h2>{view.nextAdventure.title}</h2>
          <p>{view.nextAdventure.premise}</p>
          <p>
            <strong>Known hook:</strong> {view.nextAdventure.knownHook}
          </p>
          <ScopedLink segment="">Continue campaign</ScopedLink>
        </section>
      )}
    </div>
  );
}
function useOverview(port: AdventurePort, scope: Scope, epoch: string) {
  const { store } = usePlay();
  const storageKey = `wayfarer:recap:${store.transport.principalId}:${key(scope, epoch)}`;
  const [since] = useState(() => {
    try {
      return sessionStorage.getItem(storageKey);
    } catch {
      return null;
    }
  });
  const result = useQuery({
    queryKey: [
      "adventure",
      store.transport.principalId,
      key(scope, epoch),
      "overview",
      since,
    ],
    queryFn: ({ signal }) => port.overview(scope, epoch, since, signal),
    refetchInterval: 1500,
  });
  return {
    ...result,
    acknowledge: () => {
      if (result.data) {
        try {
          sessionStorage.setItem(storageKey, result.data.recap.checkpoint);
        } catch {
          /* Optional resume storage. */
        }
      }
    },
  };
}
function EncounterControls({
  port,
  scope,
  epoch,
}: {
  port: AdventurePort;
  scope: Scope;
  epoch: string;
}) {
  const { state, store } = usePlay();
  const client = useQueryClient();
  const view = useOverview(port, scope, epoch);
  const [error, setError] = useState<string | null>(null);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const encounter = view.data?.encounter;
  async function decide(command: DecisionCommand) {
    setError(null);
    try {
      await store.decideEncounter(command);
      await client.invalidateQueries({ queryKey: ["adventure"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Decision unavailable.");
      await view.refetch();
    }
  }
  if (view.isPending) return <p role="status">Loading encounter…</p>;
  if (view.error)
    return (
      <p role="alert">
        Encounter unavailable.{" "}
        <Button onClick={() => void view.refetch()}>Reload encounter</Button>
      </p>
    );
  return (
    <section className="scene-card" aria-label="Encounter">
      <span className="eyebrow">Encounter</span>
      {encounter ? (
        <>
          <h2>
            {encounter.mode} · Round {encounter.round}
          </h2>
          <p>
            Turn:{" "}
            {state.snapshot?.characters.find(
              (c) => c.id === encounter.activeActor,
            )?.name ?? encounter.activeActor}
          </p>
          <h3>{encounter.prompt}</h3>
          {encounter.choices.map((choice) => (
            <div className="adventure-choice" key={choice.id}>
              <label>
                Target for {choice.label}
                <select
                  value={targets[choice.id] ?? ""}
                  onChange={(e) =>
                    setTargets({ ...targets, [choice.id]: e.target.value })
                  }
                >
                  <option value="">Choose target</option>
                  {choice.targets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} — {t.range}
                    </option>
                  ))}
                </select>
              </label>
              <details>
                <summary>Rules trace: {choice.label}</summary>
                <p>{choice.trace}</p>
              </details>
              <Button
                disabled={
                  !targets[choice.id] ||
                  !store.canSend("text") ||
                  encounter.activeActor !== scope.actorId ||
                  !!store.encounterRetry
                }
                onClick={() =>
                  void decide({
                    commandId: crypto.randomUUID(),
                    scope,
                    epoch,
                    version: encounter.version,
                    decisionId: encounter.decisionId,
                    choiceId: choice.id,
                    targetId: targets[choice.id]!,
                  })
                }
              >
                {choice.label}
              </Button>
            </div>
          ))}
        </>
      ) : (
        <h2>No pending encounter decision</h2>
      )}
      {store.encounterRetry && (
        <Button
          disabled={state.busy}
          onClick={() => void decide(store.encounterRetry!)}
        >
          Retry recorded decision
        </Button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
export function DiscoveryJournal() {
  const { state, store } = usePlay();
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const campaign = params.get("campaign");
    if (campaign && !state.selectedId && !state.loading && !state.expired)
      void store.select(campaign, params.get("actor"));
  }, [store, state.selectedId, state.loading, state.expired]);
  return (
    <Boundary>
      {(port, scope, epoch) => (
        <JournalContents
          key={key(scope, epoch)}
          port={port}
          scope={scope}
          epoch={epoch}
        />
      )}
    </Boundary>
  );
}
function JournalContents({
  port,
  scope,
  epoch,
}: {
  port: AdventurePort;
  scope: Scope;
  epoch: string;
}) {
  const { store } = usePlay();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<JournalKind | "all">("all");
  const [id, setId] = useState(() =>
    new URLSearchParams(location.search).get("entry"),
  );
  const view = useOverview(port, scope, epoch);
  const prefix = ["adventure", store.transport.principalId, key(scope, epoch)];
  const entries = useQuery({
    queryKey: [...prefix, "search", query, kind, view.data?.recap.checkpoint],
    queryFn: ({ signal }) => port.search(scope, epoch, query, kind, signal),
  });
  const entry = useQuery({
    queryKey: [...prefix, "entry", id],
    enabled: !!id,
    queryFn: ({ signal }) => port.entry(scope, epoch, id!, signal),
    retry: false,
  });
  // Keep filters while one is applied so a query that matches nothing is undoable.
  const searchable =
    query !== "" || kind !== "all" || (entries.data?.length ?? 1) > 0;
  function select(id: string | null) {
    setId(id);
    const url = new URL(location.href);
    if (id) url.searchParams.set("entry", id);
    else url.searchParams.delete("entry");
    history.replaceState(history.state, "", url);
  }
  return (
    <div className="adventure-journal">
      <section className="scene-card">
        <h2>Known objectives</h2>
        {view.data?.objectives.map((o) => (
          <p key={o.id}>
            <strong>{o.title}</strong> · {o.progress} · {o.status}
          </p>
        ))}
        <h2>What changed</h2>
        {view.isPending ? (
          <p>Loading recap…</p>
        ) : view.error ? (
          <p role="alert">
            Recap unavailable.{" "}
            <Button onClick={() => void view.refetch()}>Reload recap</Button>
          </p>
        ) : (
          <>
            {view.data?.recap.reset && (
              <p>
                Your previous checkpoint is unavailable. Showing the permitted
                recap.
              </p>
            )}
            {view.data?.recap.changes.length ? (
              <ul>
                {view.data.recap.changes.map((change) => (
                  <li key={change}>{change}</li>
                ))}
              </ul>
            ) : (
              <p>No new known changes.</p>
            )}
            <Button variant="outline" onClick={view.acknowledge}>
              Mark recap read for next visit
            </Button>
          </>
        )}
      </section>
      <section className="scene-card">
        <h2>Discovery journal</h2>
        {searchable && (
          <>
            <label>
              Search discoveries
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <label>
              Discovery type
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as JournalKind | "all")}
              >
                {["all", "npc", "location", "clue", "commitment"].map((k) => (
                  <option key={k}>{k}</option>
                ))}
              </select>
            </label>
          </>
        )}
        {entries.isPending ? (
          <p role="status">Searching…</p>
        ) : entries.error ? (
          <p role="alert">
            Journal unavailable.{" "}
            <Button onClick={() => void entries.refetch()}>Retry search</Button>
          </p>
        ) : (
          <>
            <p>{entries.data.length} known entries</p>
            <ul>
              {entries.data.map((e) => (
                <li key={e.id}>
                  <Button variant="outline" onClick={() => select(e.id)}>
                    {e.title}
                  </Button>{" "}
                  · {e.kind}
                </li>
              ))}
            </ul>
          </>
        )}
        {id && (
          <article aria-label="Journal entry">
            <Button variant="outline" onClick={() => select(null)}>
              Close entry
            </Button>
            {entry.isPending ? (
              <p>Loading entry…</p>
            ) : entry.error ? (
              <p role="alert">Journal entry unavailable.</p>
            ) : (
              <>
                <h3>{entry.data.title}</h3>
                <p>{entry.data.text}</p>
                <p>Learned {entry.data.learnedAt}</p>
                <a href={port.entryHref(scope, entry.data.id)}>
                  Open permanent entry link
                </a>
              </>
            )}
          </article>
        )}
      </section>
    </div>
  );
}
