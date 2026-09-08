import { LiveTransport } from "./live";
import { NetworkPlayTransport } from "../api/play-transport";
import { OnboardingPanel } from "../onboarding/panel";
import { VoiceMic } from "../voice/input";
import { voiceStatus } from "../voice/status";
import { NarrationControls } from "../voice/narration";
import { useVoice, useVoiceState } from "../voice/use-voice";
import type { VoiceController } from "../voice/controller";
import { LiveControls } from "./live-controls";
import { TacticalControls } from "./tactical";
import {
  EncounterPanel,
  DiscoveryJournal,
  SessionClosure,
} from "../adventure/pages";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ScopedLink } from "../scoped-link";
import {
  ArrowUp,
  ChevronRight,
  MessageCircle,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { usePlay } from "./use-play";
import type { Entry, PlayState, PlayStore } from "./store";
import type { Channel, Intent } from "./transport";
import { MultiplayerPanel } from "../multiplayer/panel";
import { pagePath } from "../routes";
import { rememberCampaign } from "./session";
import { TechnicalDetails } from "../components/technical-details";
import { EmptyRegion, UnavailableRegion } from "../components/region-state";
import {
  ageLabel,
  campaignPhaseLabel,
  changedLabel,
  conditionLabel,
  encumbranceLabel,
  sceneDescription,
  timestampLabel,
} from "../presentation/labels";
import { capabilityReason } from "../presentation/availability";
export function CampaignHome() {
  const { state, store } = usePlay();
  const navigate = useNavigate();
  const open = async (id: string) => {
    rememberCampaign(id);
    await store.select(id);
    await navigate({ to: pagePath(id, "") });
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
      {store.transport.adventure && <SessionClosure />}
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
  const load = encumbranceLabel(inventory?.encumbrance);
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
          {c.conditions.length > 0 && (
            <p>{c.conditions.map((x) => conditionLabel(x).label).join(", ")}</p>
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
            {load ? `Encumbrance: ${load}` : "Encumbrance not reported"} ·{" "}
            {inventory.total_weight_grams} g
          </p>
        </>
      ) : (
        <p>Equipment will appear with your character.</p>
      )}
      <TechnicalDetails
        entries={[
          { label: "Character version", value: c?.version ?? "" },
          { label: "Inventory version", value: inventory?.version ?? "" },
        ]}
      />
    </div>
  );
}
export function Journal() {
  const { store, state } = usePlay();
  if (!store.transport.adventure)
    return (
      <section className="scene-card">
        <h2>Session recap</h2>
        {state.snapshot?.session?.summary ? (
          <p>{state.snapshot.session.summary}</p>
        ) : !state.snapshot && state.error && state.selectedId ? (
          <UnavailableRegion
            heading="The recap did not load"
            reason={state.error}
            busy={state.busy}
            retryLabel="Load the recap again"
            onRetry={() => void store.select(state.selectedId!)}
          >
            Your story is safe; we could not reach the game to read it back.
          </UnavailableRegion>
        ) : (
          <EmptyRegion>
            Your story so far will be summarised here. Play a scene, and the
            recap fills in as the session goes on.
          </EmptyRegion>
        )}
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
export function Transcript({ entries }: { entries: Entry[] }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(entries.length / 50));
  const current = Math.min(page, pages - 1);
  const end = entries.length - current * 50;
  const start = Math.max(0, end - 50);
  return (
    <>
      <div className="context-actions" aria-label="Transcript pages">
        <Button
          variant="outline"
          disabled={current >= pages - 1}
          onClick={() => setPage(current + 1)}
        >
          Older entries
        </Button>
        <p role="status">
          Entries {start + 1}–{end} of {entries.length}
        </p>
        <Button
          variant="outline"
          disabled={current === 0}
          onClick={() => setPage(current - 1)}
        >
          Newer entries
        </Button>
      </div>
      <ol className="transcript" start={start + 1}>
        {entries.slice(start, end).map((entry) => (
          <ActionEntry key={entry.id} entry={entry} />
        ))}
      </ol>
    </>
  );
}

/**
 * What was submitted, when, then what the game master made of it, and only then
 * how the engine recorded it (#202, #294). The narrative answer is the body of
 * a turn: a player who wrote a sentence is owed a reply in prose, and the
 * mechanical detail is what that reply is an account of, not a substitute for
 * it. The frozen v1 action carries neither the intent text nor the narration, so
 * a turn taken on another device — or before this browser's storage was cleared
 * — says so rather than borrowing a placeholder that makes every entry read
 * alike.
 */
function ActionEntry({ entry }: { entry: Entry }) {
  const a = entry.action;
  const label = {
    action: "Your action",
    dialogue: "Character dialogue",
    ooc: "Out of character",
  }[entry.channel];
  const at = a?.created_at ?? entry.at;
  const committed = a?.status === "succeeded";
  return (
    <li className="transcript-entry">
      <div className={`player-message channel-${entry.channel}`}>
        <span className="eyebrow">{label}</span>
        {entry.text ? (
          <p>{entry.text}</p>
        ) : (
          <p className="entry-unrecorded">
            {a
              ? "This turn's text was not kept on this device."
              : "Waiting for this request to be acknowledged."}
          </p>
        )}
        {at && (
          <time className="entry-time" dateTime={at}>
            {timestampLabel(at)}
          </time>
        )}
      </div>
      {entry.narration ? (
        <div className="gm-message">
          <span className="eyebrow">
            Game master
            {entry.narration.status === "provisional"
              ? " · still writing"
              : entry.narration.status === "failed"
                ? " · narration unavailable"
                : ""}
          </span>
          <p>{entry.narration.text}</p>
          {entry.narration.status === "provisional" && (
            <small>Story text does not change the committed result.</small>
          )}
        </div>
      ) : (
        committed && (
          <div className="gm-message" role="status">
            <span className="eyebrow">Game master</span>
            <p className="entry-unrecorded">The game master is writing…</p>
          </div>
        )
      )}
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
                  ? a.waitingForSharedTime
                    ? "Waiting for shared-time coordination"
                    : "Resolving — not committed"
                  : a.status === "rejected"
                    ? "Rejected — no game changes"
                    : "Cancelled"}
      </div>
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
            {/* What changed, named for a reader. The identifiers and content
                digests that name the same things to the service are engine
                bookkeeping and sit behind the usual disclosure (#296). */}
            {a.resolution.changed_resources.length > 0 && (
              <p>Updated {changedLabel(a.resolution.changed_resources)}.</p>
            )}
            <p>Game time: {a.resolution.game_time.ticks} ticks</p>
            <TechnicalDetails
              entries={a.resolution.changed_resources.map((resource) => ({
                label: `${resource.resource_type} · ${resource.resource_id} · version`,
                value: resource.version,
              }))}
            />
          </details>
        </section>
      )}
    </li>
  );
}
/**
 * A turn the engine refused reached no game state, so it is not part of the
 * story: it belongs at the point of submission, where the player can act on it,
 * and it leaves when they do. Its diagnostics stay reachable for support without
 * standing in the narrative log for the rest of the campaign (#298).
 */
function FailedAttempts() {
  const { state, store } = usePlay();
  if (!state.attempts.length) return null;
  const last = state.attempts[state.attempts.length - 1]!;
  // Only the turn this device just sent is news. A refusal read back from an
  // earlier session is history, and history goes in the disclosure.
  const latest = last.id === state.actedId ? last : null;
  const earlier = latest ? state.attempts.slice(0, -1) : state.attempts;
  const a = latest?.action;
  const message =
    a?.status === "rejected" ? a.error.message : "This turn was cancelled.";
  return (
    <section className="failed-attempts" aria-label="Failed attempts">
      {latest && a && (
        <div role="alert">
          <p>{message}</p>
          <p className="entry-unrecorded">
            Nothing in your story changed.{" "}
            {latest.text ? "Your words are kept" : "Nothing was kept"}
            {latest.text ? ", so you can send them again." : "."}
          </p>
          <div className="context-actions">
            {!!latest.text.trim() && (
              <Button
                type="button"
                disabled={
                  !store.canSend(latest.channel === "ooc" ? "question" : "text")
                }
                onClick={() => void store.retryAttempt(latest.id)}
              >
                Try again
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              onClick={() => store.dismissAttempt(latest.id)}
            >
              Dismiss
            </Button>
          </div>
          {a.status === "rejected" && (
            <details>
              <summary>Error details</summary>
              <p>Code: {a.error.code}</p>
              <p>Request ID: {a.error.request_id}</p>
              <p>
                {a.error.retryable
                  ? "Retry is available."
                  : "Resolve the issue before retrying."}
              </p>
            </details>
          )}
        </div>
      )}
      {earlier.length > 0 && (
        <details className="attempt-diagnostics">
          <summary>
            {latest ? "Earlier failed" : "Failed"} attempts ({earlier.length})
          </summary>
          <ul>
            {earlier.map((attempt) => (
              <li key={attempt.id}>
                <time dateTime={attempt.at}>{timestampLabel(attempt.at)}</time>{" "}
                ·{" "}
                {attempt.action.status === "rejected"
                  ? `${attempt.action.error.message} (${attempt.action.error.code})`
                  : "Cancelled"}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
/**
 * Reaching the end of an adventure is the best moment the game has, and it is
 * not a permission failure. It names the outcome, says what the session came to
 * and offers the ways onward, instead of a disabled field under a sentence
 * about what this campaign will not accept (#297).
 */
function CampaignComplete() {
  const { state } = usePlay();
  const s = state.snapshot!;
  const turns = state.entries.filter(
    (e) => e.action?.status === "succeeded",
  ).length;
  return (
    <section className="scene-card campaign-complete">
      <span className="eyebrow">{campaignPhaseLabel(s.campaign.status)}</span>
      <h2>Adventure complete</h2>
      <p>
        {s.campaign.name} is over. You saw it through: {s.campaign.premise}
      </p>
      {s.session?.summary && (
        <p className="scene-description">{s.session.summary}</p>
      )}
      <p>
        {turns === 1 ? "One turn" : `${turns} turns`} played ·{" "}
        {s.campaign.game_time.ticks} ticks of game time · last scene{" "}
        {s.scene.title}
      </p>
      <div className="context-actions">
        <Button asChild>
          <ScopedLink segment="campaign">Start another adventure</ScopedLink>
        </Button>
        <Button variant="outline" asChild>
          <ScopedLink segment="journal">Review this session</ScopedLink>
        </Button>
      </div>
    </section>
  );
}
/**
 * One composer for one turn (#195-#198): the suggested actions the engine will
 * accept, the channel, speech input and the free-text field are one region with
 * one primary action. Voice is an input method here, not a parallel channel:
 * its transcript lands in this field, for review, and leaves through this Send.
 */
function Composer({ voice }: { voice: VoiceController }) {
  const { state, store } = usePlay();
  const [channel, setChannel] = useState<Channel>("action");
  const speech = useVoiceState(voice);
  useEffect(() => {
    voice.setChannel(channel);
  }, [voice, channel]);
  const draft = state.drafts[channel];
  const kind = channel === "ooc" ? "question" : "text";
  // One reason, the one that actually applies, for the whole input group.
  const blocked = store.sendBlockReason(kind);
  const max = channel === "dialogue" ? 1975 : 2000;
  const reviewing = speech.capture === "review";
  const capturing = ["requesting", "listening", "interpreting"].includes(
    speech.capture,
  );
  // The field holds one text: the typed draft, or the transcript under review.
  // A reviewed transcript is memory-only and never reaches device draft storage.
  const text = speech.capture === "idle" ? draft.text : speech.transcript;
  const status = voiceStatus(speech);
  // A draft has a visible lifecycle: what it is, how old it is, and one control
  // that throws it away (#201).
  const age = draft.savedAt ? ageLabel(draft.savedAt) : "";
  // An empty field has no draft to describe, so it says nothing about storage:
  // the counter stands alone until there is something held to report (#272).
  const held =
    reviewing || capturing
      ? "Voice transcript, not saved"
      : !draft.text
        ? ""
        : age
          ? `Draft saved on this device ${age}`
          : "Draft saved on this device";
  const submit = async () => {
    if (reviewing) await voice.submit();
    else await store.send(channel, draft.text);
  };
  const send = async (e: FormEvent) => {
    e.preventDefault();
    await submit();
  };
  // The one condition that decides whether this turn can leave, shared by the
  // Send control and the Enter key so they never disagree (#271).
  const sendable =
    !blocked && !capturing && !!text.trim() && text.trim().length <= max;
  const name =
    channel === "action"
      ? "action"
      : channel === "dialogue"
        ? "dialogue"
        : "question";
  return (
    <form className="composer" onSubmit={(e) => void send(e)}>
      <SceneSuggestions blocked={blocked} />
      <label htmlFor="play-draft">
        {channel === "action"
          ? "What do you do?"
          : channel === "dialogue"
            ? "What does your character say?"
            : "Ask an out-of-character question"}
      </label>
      <textarea
        id="play-draft"
        className={capturing ? "capturing" : undefined}
        rows={3}
        maxLength={max}
        value={text}
        disabled={!!blocked}
        readOnly={capturing}
        aria-describedby={
          blocked ? "composer-block composer-keys" : "composer-keys"
        }
        onChange={(e) => {
          if (reviewing) voice.edit(e.target.value);
          else store.saveDraft(channel, e.target.value);
        }}
        // Enter sends the turn and Shift+Enter breaks the line, the way every
        // other message field a player has used behaves (#271). A composition
        // still in progress belongs to the input method, not to the table.
        onKeyDown={(e) => {
          if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing)
            return;
          e.preventDefault();
          if (sendable) void submit();
        }}
        placeholder={
          channel === "action"
            ? "Describe your next action…"
            : channel === "dialogue"
              ? "Speak in character…"
              : "Ask the game master…"
        }
      />
      {capturing && (
        <p className="voice-wave" aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </p>
      )}
      <div className="composer-bottom">
        <fieldset
          className="channel-switch"
          disabled={state.busy || !!state.retry || capturing}
        >
          <legend className="visually-hidden">Message channel</legend>
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
        </fieldset>
        <VoiceMic voice={voice} state={speech} />
        <Button disabled={!sendable}>
          <ArrowUp size={18} aria-hidden="true" />
          Send {reviewing ? `reviewed ${name}` : name}
        </Button>
      </div>
      {/* What the field holds, how old it is, and the one control that throws
          it away — beneath the toolbar, so the turn keeps one primary action. */}
      <p className="composer-draft-state">
        <span className="composer-count">
          {text.length} / {max}
          {held && ` · ${held}`}
        </span>
        {/* The keyboard path to Send is discoverable from the composer itself,
            beside the count it shares a line with (#271). */}
        <span className="composer-keys" id="composer-keys">
          Enter sends · Shift+Enter starts a new line
        </span>
        {!!draft.text && !reviewing && !capturing && (
          <Button
            type="button"
            variant="outline"
            className="discard-draft"
            onClick={() => store.discardDraft(channel)}
          >
            <Trash2 size={16} aria-hidden="true" />
            Discard draft
          </Button>
        )}
      </p>
      <p className="voice-status" role="status" aria-live="polite">
        {status}
      </p>
      {speech.error && <p role="alert">{speech.error}</p>}
      {reviewing && text.trim().length > max && (
        <p role="alert">Transcript is too long. Shorten it before sending.</p>
      )}
      {!voice.speech.recognitionAvailable && (
        <p className="composer-hint">
          Microphone speech recognition is unsupported or requires a secure
          browser context. Type your turn instead.
        </p>
      )}
      {blocked && (
        <p className="composer-hint" id="composer-block">
          {blocked}
        </p>
      )}
    </form>
  );
}
/**
 * Scene actions the engine will actually accept. An action kind the campaign
 * does not advertise is never rendered as a control: its observations stay
 * visible as scene detail with the reason, so no click buys a rejection.
 */
function sceneActions(state: PlayState, store: PlayStore) {
  const s = state.snapshot!;
  // A campaign that names its inspectable targets is taken at its word; one that
  // names none falls back to the plain capability.
  const targeted = s.campaign.capabilities.some((c) =>
    c.startsWith("actions.inspect:"),
  );
  const supports = (kind: Intent["kind"], id: string) =>
    kind === "inspect" && targeted
      ? s.campaign.capabilities.includes(`actions.inspect:${id}`)
      : s.campaign.capabilities.includes(`actions.${kind}`);
  const observations = s.scene.observations.map((o) => {
    const exit = o.description === "Known scene exit";
    return {
      id: o.id,
      name: o.label,
      kind: (exit ? "move" : "inspect") as Intent["kind"],
      label: `${exit ? "Travel to" : "Inspect"} ${o.label}`,
      intent: (exit
        ? { kind: "move", destination_id: o.id }
        : { kind: "inspect", target_id: o.id }) as Intent,
    };
  });
  // The engine transports drive their own waiting; only the frozen slice offers it.
  const waiting =
    !(store.transport instanceof LiveTransport) &&
    !(
      store.transport instanceof NetworkPlayTransport &&
      store.transport.engineTransport
    );
  const offered = observations.filter((o) => supports(o.kind, o.id));
  const withheld = observations.filter((o) => !supports(o.kind, o.id));
  // Every remaining control shares one temporary blocker, so state it once.
  const pending = [
    ...new Set(
      [...(waiting ? ["wait" as const] : []), ...offered.map((o) => o.kind)]
        .map((kind) => store.sendBlockReason(kind))
        .filter((reason): reason is string => reason !== null),
    ),
  ];
  return { waiting, offered, withheld, pending };
}
/**
 * The suggested actions and the free-text field are two ways to take the same
 * turn, so they are one decision in one place, directly above the input (#197).
 * They share the composer's condition when it is the same condition, and the
 * composer states it: the notice belongs once, beside the control the player
 * would reach for, not above the input and below it (#297).
 */
function SceneSuggestions({ blocked }: { blocked: string | null }) {
  const { state, store } = usePlay();
  if (!state.snapshot) return null;
  const { waiting, offered, pending: reasons } = sceneActions(state, store);
  const pending = reasons.filter((reason) => reason !== blocked);
  if (!waiting && !offered.length) return null;
  return (
    <div className="composer-suggestions">
      <div className="context-actions" aria-label="Suggested actions">
        {waiting && (
          <Button
            type="button"
            variant="outline"
            disabled={!store.canSend("wait")}
            title={store.sendBlockReason("wait") ?? undefined}
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
        {offered.map((o) => (
          <Button
            type="button"
            key={o.id}
            variant="outline"
            disabled={!store.canSend(o.kind)}
            title={store.sendBlockReason(o.kind) ?? undefined}
            onClick={() => void store.send("action", o.label, o.intent)}
          >
            {o.label}
          </Button>
        ))}
      </div>
      {pending.map((reason) => (
        <p key={reason} className="composer-hint">
          {reason}
        </p>
      ))}
    </div>
  );
}
/** An observation with no control stays readable as scene detail, with its reason. */
function SceneObservations() {
  const { state, store } = usePlay();
  if (!state.snapshot) return null;
  const { withheld } = sceneActions(state, store);
  const kinds = [...new Set(withheld.map((o) => o.kind))];
  return (
    <>
      {kinds.map((kind) => (
        <div key={kind} className="scene-withheld">
          <p>{capabilityReason(kind)}</p>
          <ul>
            {withheld
              .filter((o) => o.kind === kind)
              .map((o) => (
                <li key={o.id}>{o.name}</li>
              ))}
          </ul>
        </div>
      ))}
    </>
  );
}
/** A select implies a choice; one controlled character is a statement (#198). */
function ActingAs() {
  const { state, store } = usePlay();
  const s = state.snapshot;
  if (!s) return null;
  const controlled = s.characters.filter(
    (c) =>
      s.campaign.membership.actor_ids.includes(c.id) &&
      s.scene.visible_actor_ids.includes(c.id),
  );
  if (controlled.length < 2)
    return (
      <p className="acting-as">
        {controlled.length ? (
          <>
            Acting as <strong>{controlled[0]!.name}</strong>
          </>
        ) : (
          "No controlled character"
        )}
      </p>
    );
  return (
    <label className="actor-select">
      Acting as
      <select
        value={state.actorId ?? ""}
        disabled={
          state.busy ||
          !!state.retry ||
          state.entries.some((e) => e.action?.status === "needs_clarification")
        }
        onChange={(e) => store.chooseActor(e.target.value)}
      >
        {!state.actorId && <option value="">No controlled character</option>}
        {controlled.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  );
}
export function PlayWorkspace() {
  const { state, store } = usePlay();
  const voice = useVoice();
  const s = state.snapshot;
  // The projection echoes the location name into the required description
  // field; the card prints the name once (#203).
  const description = s
    ? sceneDescription(s.scene.title, s.scene.description)
    : null;
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
          <ScopedLink segment="campaign">Choose a campaign</ScopedLink>
        </Button>
      </section>
    );
  return (
    <div className="play-workspace">
      <LiveControls />
      <TacticalControls />
      <MultiplayerPanel />
      <EncounterPanel />
      {store.transport.sample && (
        <p className="sample-note">Sample story · Fixed outcomes for preview</p>
      )}
      <section className="scene-card current-scene">
        <span className="eyebrow">{s.campaign.name}</span>
        <h2>{s.scene.title}</h2>
        {description && <p className="scene-description">{description}</p>}
        <SceneObservations />
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
        <div className="transcript-header">
          <h2 className="transcript-heading">
            <MessageCircle size={20} aria-hidden="true" />
            At the table
          </h2>
          {/* Narration is playback of what appears here, not composition (#195). */}
          <NarrationControls voice={voice} />
        </div>
        {!state.entries.length ? (
          <p className="transcript-empty">
            The scene is set. What happens next begins with you.
          </p>
        ) : (
          <Transcript
            key={`${s.campaign.id}:${s.scene.id}:${state.actorId}`}
            entries={state.entries}
          />
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
      {s.campaign.status === "completed" ? (
        <CampaignComplete />
      ) : (
        <>
          <FailedAttempts />
          <ActingAs />
          <Composer
            voice={voice}
            key={`${s.campaign.id}:${s.scene.id}:${state.actorId}:${s.campaign.membership.version}`}
          />
        </>
      )}
    </div>
  );
}
