import { LiveTransport } from "./live";
import { OnboardingPanel } from "../onboarding/panel";
import { VoicePanel } from "../voice/panel";
import { LiveControls } from "./live-controls";
import { EncounterPanel, DiscoveryJournal } from "../adventure/pages";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowUp, ChevronRight, MessageCircle, Sparkles } from "lucide-react";
import { Button } from "../components/ui/button";
import { usePlay } from "./use-play";
import type { Entry } from "./store";
import type { Channel } from "./transport";
import { MultiplayerPanel } from "../multiplayer/panel";
export function CampaignHome() {
  const { state, store } = usePlay();
  const navigate = useNavigate();
  const open = async (id: string) => {
    await store.select(id);
    await navigate({ to: "/" });
  };
  if (state.expired) return <SessionExpired />;
  return (
    <section className="campaign-home">
      <OnboardingPanel />
      <div className="section-intro">
        <h2>Your campaigns</h2>
        <p>Return to a story, or choose another table.</p>
      </div>
      {state.loading && !state.snapshot && (
        <p role="status">Loading campaigns…</p>
      )}
      {state.error && (
        <div role="alert">
          <p>{state.error}</p>
          <Button onClick={() => void store.loadCampaigns()}>Try again</Button>
        </div>
      )}
      {!state.campaigns.length && !state.loading ? (
        <div className="scene-card">
          <h2>No campaign selected</h2>
          <p>Your campaigns will appear when a connection is available.</p>
        </div>
      ) : (
        <div className="campaign-grid">
          {state.campaigns.map((c) => (
            <article key={c.id} className="campaign-card">
              <div className="card-top">
                <span className="eyebrow">{c.status}</span>
                {store.transport.sample && (
                  <span className="sample-label">Sample story</span>
                )}
              </div>
              <h2>{c.name}</h2>
              <p>{c.premise}</p>
              <div className="card-bottom">
                <span>{c.membership.role}</span>
                <Button onClick={() => void open(c.id)}>
                  {store.resumeId() === c.id
                    ? "Resume campaign"
                    : "Open campaign"}
                  <ChevronRight size={18} aria-hidden="true" />
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}
      {store.transport.sample && (
        <p className="sample-note">
          Sample mode uses a fixed story outcome to preview the workspace. It is
          not a live game.
        </p>
      )}
    </section>
  );
}
function SessionExpired() {
  return (
    <section className="scene-card" role="alert">
      <h2>Session ended</h2>
      <p>
        Your session expired or access changed. Private campaign data and drafts
        have been cleared. Reconnect through your campaign connection to
        continue.
      </p>
    </section>
  );
}
export function CharacterSummary() {
  const { state } = usePlay();
  const s = state.snapshot;
  const c = s?.characters.find((c) => c.id === state.actorId);
  const inventory = s?.inventories.find((i) => i.actor_id === state.actorId);
  return (
    <div className="context-details">
      <h3>{c?.name ?? "Character"}</h3>
      {c ? (
        <>
          <dl className="stat-grid">
            <div>
              <dt>HP</dt>
              <dd>
                {c.hp.current} / {c.hp.maximum}
              </dd>
            </div>
            <div>
              <dt>FP</dt>
              <dd>
                {c.fp.current} / {c.fp.maximum}
              </dd>
            </div>
          </dl>
          <p className="resource-version">Character version {c.version}</p>
          {c.conditions.length > 0 && (
            <p>{c.conditions.map((x) => x.label).join(", ")}</p>
          )}
        </>
      ) : (
        <p>No character selected.</p>
      )}
      <h3>Inventory</h3>
      {inventory ? (
        <>
          <ul className="inventory-summary">
            {inventory.items.map((item) => (
              <li key={item.id}>
                <span>{item.name}</span>
                <strong>× {item.quantity}</strong>
              </li>
            ))}
          </ul>
          <p>
            {inventory.encumbrance} encumbrance · {inventory.total_weight_grams}{" "}
            g
          </p>
          <p className="resource-version">
            Inventory version {inventory.version}
          </p>
        </>
      ) : (
        <p>Equipment will appear with your character.</p>
      )}
    </div>
  );
}
export function Journal() {
  const { store, state } = usePlay();
  if (!store.transport.adventure)
    return (
      <section className="scene-card">
        <h2>Session recap</h2>
        <p>
          {state.snapshot?.session?.summary ??
            "No session recap is available yet."}
        </p>
      </section>
    );
  return <DiscoveryJournal />;
}
function Clarification({ entry }: { entry: Entry }) {
  const { state, store } = usePlay();
  const [answer, setAnswer] = useState("");
  const a = entry.action;
  if (a?.status !== "needs_clarification") return null;
  const disabled =
    state.connection !== "online" ||
    state.busy ||
    !!state.retry ||
    !!state.tableRetry;
  return (
    <section className="clarification" aria-label="Pending choice">
      <h3>A detail before we continue</h3>
      <p>{a.clarification.prompt}</p>
      <div className="context-actions">
        {a.clarification.choices.map((c) => (
          <Button
            variant="outline"
            disabled={disabled}
            key={c.id}
            onClick={() => void store.clarify(entry.id, { choice_id: c.id })}
          >
            {c.label}
          </Button>
        ))}
      </div>
      {a.clarification.allows_text && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void store.clarify(entry.id, { text: answer.trim() });
          }}
        >
          <label htmlFor={`answer-${entry.id}`}>
            Or clarify in your own words
          </label>
          <input
            id={`answer-${entry.id}`}
            value={answer}
            maxLength={2000}
            disabled={disabled}
            onChange={(e) => setAnswer(e.target.value)}
          />
          <Button disabled={disabled || !answer.trim()}>
            Answer clarification
          </Button>
        </form>
      )}
      <p className="resource-version">Continues action {a.id}</p>
    </section>
  );
}
function ActionEntry({ entry }: { entry: Entry }) {
  const a = entry.action;
  const label = {
    action: "Your action",
    dialogue: "Character dialogue",
    ooc: "Out of character",
  }[entry.channel];
  return (
    <li className="transcript-entry">
      <div className={`player-message channel-${entry.channel}`}>
        <span className="eyebrow">{label}</span>
        <p>{entry.text}</p>
      </div>
      <div className="action-status" role="status">
        {!a
          ? "Awaiting acknowledgement"
          : a.status === "succeeded"
            ? a.mechanicallyCommitted === false
              ? "Answered — no game changes"
              : "Committed"
            : a.status === "needs_clarification"
              ? "Awaiting your clarification"
              : a.status === "submitted"
                ? "Submitted — not committed"
                : a.status === "resolving"
                  ? "Resolving — not committed"
                  : a.status === "rejected"
                    ? "Rejected — no game changes"
                    : "Cancelled"}
      </div>
      {a?.status === "rejected" && <p role="alert">{a.error.message}</p>}
      <Clarification entry={entry} />
      {a?.status === "succeeded" && (
        <section className="committed-result">
          <h3>Authoritative result</h3>
          <p>{a.resolution.summary}</p>
          <details>
            <summary>Rolls and consequences</summary>
            {a.resolution.checks.length === 0 ? (
              <p>No roll was required.</p>
            ) : (
              a.resolution.checks.map((check, i) => (
                <p key={i}>
                  {check.label}: {check.dice.join(" + ")} against {check.target}{" "}
                  · {check.outcome.replaceAll("_", " ")} · margin {check.margin}
                </p>
              ))
            )}
            <ul>
              {a.resolution.changed_resources.map((resource) => (
                <li key={`${resource.resource_type}:${resource.resource_id}`}>
                  {resource.resource_type} · {resource.resource_id} · version{" "}
                  {resource.version}
                </li>
              ))}
            </ul>
            <p>Game time: {a.resolution.game_time.ticks} ticks</p>
          </details>
        </section>
      )}
      {entry.narration && (
        <div className="gm-message">
          <span className="eyebrow">
            Game master ·{" "}
            {entry.narration.status === "provisional"
              ? "Provisional narration"
              : entry.narration.status === "failed"
                ? "Narration unavailable"
                : "Narration"}
          </span>
          <p>{entry.narration.text}</p>
          {entry.narration.status === "provisional" && (
            <small>Story text does not change the committed result.</small>
          )}
        </div>
      )}
    </li>
  );
}
function Composer() {
  const { state, store } = usePlay();
  const [channel, setChannel] = useState<Channel>("action");
  const draft = state.drafts[channel];
  const kind = channel === "ooc" ? "question" : "text";
  const allowed = store.canSend(kind);
  const max = channel === "dialogue" ? 1975 : 2000;
  const send = async (e: FormEvent) => {
    e.preventDefault();
    await store.send(channel, draft);
  };
  return (
    <form className="composer" onSubmit={(e) => void send(e)}>
      <fieldset disabled={state.busy || !!state.retry}>
        <legend>Message channel</legend>
        <div className="channel-picker">
          {(["action", "dialogue", "ooc"] as const)
            .filter((value) => !store.transport.multiplayer || value !== "ooc")
            .map((value) => (
              <label key={value}>
                <input
                  type="radio"
                  name="channel"
                  value={value}
                  checked={channel === value}
                  onChange={() => setChannel(value)}
                />
                {value === "action"
                  ? "Action"
                  : value === "dialogue"
                    ? "Dialogue"
                    : "OOC"}
              </label>
            ))}
        </div>
      </fieldset>
      <VoicePanel key={channel} channel={channel} />
      <label htmlFor="play-draft">
        {channel === "action"
          ? "What do you do?"
          : channel === "dialogue"
            ? "What does your character say?"
            : "Ask an out-of-character question"}
      </label>
      <textarea
        id="play-draft"
        rows={3}
        maxLength={max}
        value={draft}
        disabled={state.busy || !!state.retry}
        onChange={(e) => {
          const text = e.target.value;
          store.saveDraft(channel, text);
        }}
        placeholder={
          channel === "action"
            ? "Describe your next action…"
            : channel === "dialogue"
              ? "Speak in character…"
              : "Ask the game master…"
        }
      />
      <div className="composer-bottom">
        <span>
          {draft.length} / {max} · Draft saved on this device
        </span>
        <Button disabled={!allowed || !draft.trim()}>
          <ArrowUp size={18} aria-hidden="true" />
          Send{" "}
          {channel === "action"
            ? "action"
            : channel === "dialogue"
              ? "dialogue"
              : "question"}
        </Button>
      </div>
      {!allowed && !state.busy && !state.retry && (
        <p className="composer-hint">
          {state.entries.some((e) => e.action?.status === "needs_clarification")
            ? "Answer the pending clarification to continue."
            : "This message channel requires a controlled character and campaign permission."}
        </p>
      )}
    </form>
  );
}
export function PlayWorkspace() {
  const { state, store } = usePlay();
  const s = state.snapshot;
  if (state.expired) return <SessionExpired />;
  if (state.loading)
    return (
      <section className="scene-card" role="status">
        Loading your campaign…
      </section>
    );
  if (!s)
    return (
      <section className="scene-card empty-state">
        <Sparkles size={28} aria-hidden="true" />
        <span className="eyebrow">Your next chapter</span>
        <h2>No campaign selected</h2>
        <p>
          Choose a campaign to see the current scene and return to your story.
        </p>
        {state.error && <p role="alert">{state.error}</p>}
        <Button asChild>
          <Link to="/campaign">Choose a campaign</Link>
        </Button>
      </section>
    );
  return (
    <div className="play-workspace">
      <LiveControls />
      <MultiplayerPanel />
      <EncounterPanel />
      {store.transport.sample && (
        <p className="sample-note">Sample story · Fixed outcomes for preview</p>
      )}
      <section className="scene-card current-scene">
        <span className="eyebrow">{s.campaign.name}</span>
        <h2>{s.scene.title}</h2>
        <p className="scene-description">{s.scene.description}</p>
        <div className="context-actions">
          {!(store.transport instanceof LiveTransport) && (
            <Button
              disabled={!store.canSend("wait")}
              onClick={() =>
                void store.send("action", "Wait one tick", {
                  kind: "wait",
                  ticks: 1,
                })
              }
            >
              Wait one tick
            </Button>
          )}
          {s.scene.observations.map((o) => (
            <Button
              key={o.id}
              variant="outline"
              disabled={
                !store.canSend(
                  o.description === "Known scene exit" ? "move" : "inspect",
                )
              }
              onClick={() =>
                void store.send(
                  "action",
                  `${o.description === "Known scene exit" ? "Travel to" : "Inspect"} ${o.label}`,
                  o.description === "Known scene exit"
                    ? { kind: "move", destination_id: o.id }
                    : { kind: "inspect", target_id: o.id },
                )
              }
            >
              {o.description === "Known scene exit" ? "Travel to" : "Inspect"}{" "}
              {o.label}
            </Button>
          ))}
        </div>
        <details className="session-recap">
          <summary>Session recap & known objectives</summary>
          <p>{s.session?.summary ?? "No recap yet."}</p>
          <h3>Known objectives</h3>
          {s.objectives.length ? (
            <ul>
              {s.objectives.map((o) => (
                <li key={o}>{o}</li>
              ))}
            </ul>
          ) : (
            <p>No known objectives.</p>
          )}
          <h3>Visible party</h3>
          <ul>
            {s.party
              .filter((p) => s.scene.visible_actor_ids.includes(p.id))
              .map((p) => (
                <li key={p.id}>
                  {p.name} · {p.status}
                </li>
              ))}
          </ul>
        </details>
      </section>
      <section aria-label="Play transcript">
        <h2 className="transcript-heading">
          <MessageCircle size={20} aria-hidden="true" />
          At the table
        </h2>
        {!state.entries.length ? (
          <p className="transcript-empty">
            The scene is set. What happens next begins with you.
          </p>
        ) : (
          <ol className="transcript">
            {state.entries.map((entry) => (
              <ActionEntry key={entry.id} entry={entry} />
            ))}
          </ol>
        )}
      </section>
      {state.error && (
        <div className="request-error" role="alert">
          <p>{state.error}</p>
          {state.retry ? (
            <Button disabled={state.busy} onClick={() => void store.retry()}>
              Retry same request
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={() => void store.select(s.campaign.id)}
            >
              Reload campaign
            </Button>
          )}
        </div>
      )}
      <label className="actor-select">
        Acting as
        <select
          value={state.actorId ?? ""}
          disabled={
            state.busy ||
            !!state.retry ||
            state.entries.some(
              (e) => e.action?.status === "needs_clarification",
            )
          }
          onChange={(e) => store.chooseActor(e.target.value)}
        >
          {!state.actorId && <option value="">No controlled character</option>}
          {s.characters
            .filter(
              (c) =>
                s.campaign.membership.actor_ids.includes(c.id) &&
                s.scene.visible_actor_ids.includes(c.id),
            )
            .map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
        </select>
      </label>
      <Composer
        key={`${s.campaign.id}:${s.scene.id}:${state.actorId}:${s.campaign.membership.version}`}
      />
    </div>
  );
}
