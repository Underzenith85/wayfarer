import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button } from "../components/ui/button";
import { usePlay } from "../play/use-play";
import {
  defaultSetup,
  type Build,
  type CharacterSlot,
  type Identity,
  type Intent,
  type Lobby,
  type OnboardingPort,
  type OnboardingView,
} from "./model";
export function OnboardingPanel() {
  const { store } = usePlay();
  const port = store.transport.onboarding;
  if (!port)
    return (
      <p>
        Campaign creation and onboarding are not available on this connection.
      </p>
    );
  return (
    <LobbyPanel
      key={store.transport.principalId}
      port={port}
      identity={
        store.transport.principalId.endsWith(":guest") ? "guest" : "host"
      }
    />
  );
}
function LobbyPanel({
  port,
  identity,
}: {
  port: OnboardingPort;
  identity: Identity;
}) {
  const { store } = usePlay();
  const navigate = useNavigate();
  const [view, setView] = useState<OnboardingView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState(defaultSetup);
  const [token, setToken] = useState("");
  const controller = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const lobby = view?.lobby;
  const refresh = async () => {
    controller.current?.abort();
    const c = new AbortController();
    controller.current = c;
    try {
      const value = await port.read(c.signal);
      if (!c.signal.aborted) {
        setView(value);
        setError("");
      }
    } catch (e) {
      if (!c.signal.aborted)
        setError(e instanceof Error ? e.message : "Lobby unavailable.");
    }
  };
  useEffect(() => {
    const c = new AbortController();
    controller.current = c;
    void port
      .read(c.signal)
      .then((value) => {
        if (!c.signal.aborted) setView(value);
      })
      .catch((e: unknown) => {
        if (!c.signal.aborted)
          setError(e instanceof Error ? e.message : "Lobby unavailable.");
      });
    return () => {
      controller.current?.abort();
    };
  }, [port]);
  const run = async (intent: Intent) => {
    if (pending.current) return false;
    pending.current = true;
    setBusy(true);
    setError("");
    controller.current?.abort();
    const c = new AbortController();
    controller.current = c;
    try {
      const value = await port.command(
        { id: crypto.randomUUID(), revision: lobby?.revision ?? 0, intent },
        c.signal,
      );
      if (!c.signal.aborted) {
        setView(value);
        await store.loadCampaigns();
        return true;
      }
    } catch (e) {
      if (!c.signal.aborted)
        setError(
          e instanceof Error
            ? e.message
            : "Request failed. Refresh to check whether it completed.",
        );
      return false;
    } finally {
      if (!c.signal.aborted) {
        pending.current = false;
        setBusy(false);
      }
    }
    return false;
  };
  return (
    <section
      className="scene-card onboarding-panel"
      aria-label="Campaign onboarding"
    >
      <span className="sample-label">Sample onboarding · {identity}</span>
      <h2>Begin a new story</h2>
      <p>
        Build your table, shape your characters, then step into the opening
        scene.
      </p>
      <p className="sample-note">
        This preview uses simulated generation and a limited character catalog.
        Live onboarding is not connected.
      </p>
      {error && <p role="alert">{error}</p>}
      <Button variant="outline" disabled={busy} onClick={() => void refresh()}>
        Refresh lobby
      </Button>
      {!view ? (
        <p role="status">Loading lobby…</p>
      ) : !lobby ? (
        <>
          {identity === "host" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void run({ kind: "create", setup });
              }}
            >
              <h3>1. Shape the campaign</h3>
              <label>
                Campaign name
                <input
                  required
                  maxLength={80}
                  value={setup.name}
                  onChange={(e) => setSetup({ ...setup, name: e.target.value })}
                />
              </label>
              <label>
                Premise
                <textarea
                  required
                  maxLength={2000}
                  value={setup.premise}
                  onChange={(e) =>
                    setSetup({ ...setup, premise: e.target.value })
                  }
                />
              </label>
              <div className="onboarding-grid">
                <label>
                  Tone
                  <select
                    value={setup.tone}
                    onChange={(e) =>
                      setSetup({
                        ...setup,
                        tone: e.target.value as typeof setup.tone,
                      })
                    }
                  >
                    <option value="hopeful">Hopeful</option>
                    <option value="gritty">Gritty</option>
                  </select>
                </label>
                <label>
                  Duration
                  <select
                    value={setup.duration}
                    onChange={(e) =>
                      setSetup({
                        ...setup,
                        duration: e.target.value as typeof setup.duration,
                      })
                    }
                  >
                    <option value="one-shot">One session</option>
                    <option value="short-campaign">Short campaign</option>
                  </select>
                </label>
                <label>
                  Difficulty
                  <select
                    value={setup.difficulty}
                    onChange={(e) =>
                      setSetup({
                        ...setup,
                        difficulty: e.target.value as typeof setup.difficulty,
                      })
                    }
                  >
                    <option value="standard">Standard</option>
                    <option value="challenging">Challenging</option>
                  </select>
                </label>
                <label>
                  Rules
                  <select value={setup.rules} disabled>
                    <option>wayfarer-lite-1</option>
                  </select>
                </label>
                <label>
                  Party
                  <select
                    value={setup.party}
                    onChange={(e) =>
                      setSetup({
                        ...setup,
                        party: e.target.value as typeof setup.party,
                      })
                    }
                  >
                    <option value="companions">Established companions</option>
                    <option value="strangers">
                      Strangers brought together
                    </option>
                  </select>
                </label>
                <label>
                  Scenario
                  <select
                    value={setup.scenario}
                    onChange={(e) =>
                      setSetup({
                        ...setup,
                        scenario: e.target.value as typeof setup.scenario,
                      })
                    }
                  >
                    <option value="courier">Missing courier</option>
                    <option value="lighthouse">Dark lighthouse</option>
                  </select>
                </label>
              </div>
              <Button disabled={busy}>Create campaign</Button>
            </form>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run({ kind: "join", token: token.trim() });
            }}
          >
            <h3>Join a table</h3>
            <label>
              Invitation code
              <input
                required
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            <Button disabled={busy || !token.trim()}>Join campaign</Button>
          </form>
        </>
      ) : (
        <>
          <h3>{lobby.setup.name}</h3>
          <p>{lobby.setup.premise}</p>
          <p>
            {lobby.setup.tone} · {lobby.setup.duration} ·{" "}
            {lobby.setup.difficulty} · {lobby.setup.party} · {lobby.setup.rules}
          </p>
          <aside aria-label="Scenario preview">
            <h3>Scenario preview: {lobby.preview.title}</h3>
            <p>{lobby.preview.description}</p>
            <p>Only player-visible setup information is shown.</p>
          </aside>
          <p role="status">
            Campaign {lobby.status} · Revision {lobby.revision}
          </p>
          {lobby.status === "draft" ? (
            <>
              <h3>2. Gather your party</h3>
              {identity === "host" && (
                <>
                  <Button
                    disabled={busy}
                    onClick={() => void run({ kind: "invite" })}
                  >
                    Create invitation
                  </Button>
                  {lobby.invite && (
                    <label>
                      Share this invitation code
                      <input readOnly value={lobby.invite} />
                    </label>
                  )}
                </>
              )}
              <ul>
                {lobby.members.map((m) => (
                  <li key={m.id}>
                    {m.id}: {m.ready ? "Ready" : "Not ready"}
                  </li>
                ))}
              </ul>
              <h3>3. Character workshop</h3>
              {lobby.characters.map((slot) => (
                <Workshop
                  key={`${slot.id}:${slot.owner}`}
                  slot={slot}
                  lobby={lobby}
                  identity={identity}
                  busy={busy}
                  run={run}
                />
              ))}
              <h3>4. Ready for the opening scene</h3>
              <Button
                disabled={
                  busy ||
                  !lobby.characters.some(
                    (s) =>
                      s.owner === identity && s.draft?.status === "finalized",
                  )
                }
                onClick={() =>
                  void run({
                    kind: "ready",
                    ready: !lobby.members.find((m) => m.id === identity)?.ready,
                  })
                }
              >
                {lobby.members.find((m) => m.id === identity)?.ready
                  ? "Mark not ready"
                  : "Mark ready"}
              </Button>
              {identity === "host" && (
                <Button
                  disabled={
                    busy ||
                    lobby.members.length < 2 ||
                    !lobby.members.every((m) => m.ready)
                  }
                  onClick={() => void run({ kind: "activate" })}
                >
                  Start campaign
                </Button>
              )}
            </>
          ) : (
            <Button
              disabled={busy}
              onClick={() => {
                void store
                  .select("campaign-1")
                  .then(() => navigate({ to: "/" }));
              }}
            >
              Enter opening scene
            </Button>
          )}
        </>
      )}
    </section>
  );
}
function Workshop({
  slot,
  lobby,
  identity,
  busy,
  run,
}: {
  slot: CharacterSlot;
  lobby: Lobby;
  identity: Identity;
  busy: boolean;
  run: (i: Intent) => Promise<boolean>;
}) {
  // The editor survives lobby refreshes. Remote revisions never overwrite unsaved work.
  const [build, setBuild] = useState<Build>(
    () =>
      slot.draft?.build ?? {
        name: slot.label,
        concept: "",
        strength: 10,
        dexterity: 10,
      },
  );
  const [outcome, setOutcome] = useState<"success" | "failure" | "invalid">(
    "success",
  );
  const [baseRevision, setBaseRevision] = useState(slot.draft?.revision ?? 0);
  const [dirty, setDirty] = useState(false);
  const draft = slot.draft;
  const own = slot.owner === identity;
  const locked = busy || draft?.status === "finalized";
  const stale = dirty && baseRevision !== (draft?.revision ?? 0);
  const edit = (b: Build) => {
    if (!dirty) setBaseRevision(draft?.revision ?? 0);
    setBuild(b);
    setDirty(true);
  };
  const current = dirty ? build : (draft?.build ?? build);
  const save = async (generate: boolean) => {
    const saved = await run(
      generate
        ? {
            kind: "generate",
            slot: slot.id,
            prompt: current.concept,
            draftRevision: dirty ? baseRevision : (draft?.revision ?? 0),
            outcome,
          }
        : {
            kind: "save",
            slot: slot.id,
            build: current,
            draftRevision: dirty ? baseRevision : (draft?.revision ?? 0),
          },
    );
    if (saved) setDirty(false);
  };
  return (
    <article
      className="onboarding-character"
      aria-label={`${slot.label} workshop`}
    >
      <h4>
        {slot.label} · {slot.owner ?? "Unclaimed"}
      </h4>
      {!slot.owner && (
        <div className="context-actions">
          <Button
            disabled={
              busy || lobby.characters.some((s) => s.owner === identity)
            }
            onClick={() => void run({ kind: "claim", slot: slot.id })}
          >
            Claim {slot.label}
          </Button>
          {identity === "host" &&
            lobby.members
              .filter((m) => !lobby.characters.some((s) => s.owner === m.id))
              .map((m) => (
                <Button
                  key={m.id}
                  disabled={busy}
                  onClick={() =>
                    void run({ kind: "assign", slot: slot.id, owner: m.id })
                  }
                >
                  Assign {slot.label} to {m.id}
                </Button>
              ))}
        </div>
      )}
      {own && (
        <>
          <label>
            Character name
            <input
              disabled={locked}
              value={current.name}
              maxLength={80}
              onChange={(e) => edit({ ...current, name: e.target.value })}
            />
          </label>
          <label htmlFor={`concept-${slot.id}`}>Character concept</label>
          <textarea
            id={`concept-${slot.id}`}
            disabled={locked}
            value={current.concept}
            maxLength={4000}
            onChange={(e) => edit({ ...current, concept: e.target.value })}
          />
          <div className="onboarding-grid">
            <label>
              Strength
              <input
                type="number"
                disabled={locked}
                min={8}
                max={14}
                value={current.strength}
                onChange={(e) =>
                  edit({ ...current, strength: Number(e.target.value) })
                }
              />
            </label>
            <label>
              Dexterity
              <input
                type="number"
                disabled={locked}
                min={8}
                max={14}
                value={current.dexterity}
                onChange={(e) =>
                  edit({ ...current, dexterity: Number(e.target.value) })
                }
              />
            </label>
          </div>
          <p>
            100-point budget. ST costs 10 per level; DX costs 20. IQ and HT stay
            at 10 in this preview.
          </p>
          {dirty && (
            <p role="status">
              Unsaved editor changes. Save and load the validated revision
              before finalizing.
            </p>
          )}
          {stale && (
            <p role="alert">
              A newer saved revision is available. Your editor has been
              preserved; load that revision before saving again.
            </p>
          )}
          <Button disabled={locked || stale} onClick={() => void save(false)}>
            Save and validate
          </Button>
          <label>
            Sample generation outcome
            <select
              disabled={locked}
              value={outcome}
              onChange={(e) => setOutcome(e.target.value as typeof outcome)}
            >
              <option value="success">Legal draft</option>
              <option value="failure">Generation failure</option>
              <option value="invalid">Over-budget draft</option>
            </select>
          </label>
          <Button
            disabled={locked || stale || !current.concept.trim()}
            onClick={() => void save(true)}
          >
            Generate character draft
          </Button>
        </>
      )}
      {draft && (
        <>
          <p role="status">
            Saved revision {draft.revision}: {draft.status} · {draft.spent}{" "}
            points spent · {draft.remaining} remaining
          </p>
          {draft.errors.length > 0 && (
            <ul aria-label="Legality errors">
              {draft.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
          {own && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                setBuild(draft.build);
                setBaseRevision(draft.revision);
                setDirty(false);
              }}
            >
              Load saved revision
            </Button>
          )}
          {identity === "host" && (
            <Button
              disabled={busy || draft.status !== "pending" || (own && dirty)}
              onClick={() =>
                void run({
                  kind: "approve",
                  slot: slot.id,
                  draftRevision: draft.revision,
                })
              }
            >
              Approve {slot.label}
            </Button>
          )}
          {own && (
            <Button
              disabled={busy || dirty || draft.status !== "approved"}
              onClick={() =>
                void run({
                  kind: "finalize",
                  slot: slot.id,
                  draftRevision: draft.revision,
                })
              }
            >
              Finalize {slot.label}
            </Button>
          )}
          <details>
            <summary>Saved revision history ({slot.history.length})</summary>
            {slot.history.map((d) => (
              <p key={d.revision}>
                Revision {d.revision}: {d.build.name} · ST {d.build.strength} /
                DX {d.build.dexterity} · {d.status} · {d.spent} points
              </p>
            ))}
          </details>
        </>
      )}
    </article>
  );
}
