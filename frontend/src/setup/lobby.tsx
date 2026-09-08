import {
  CharacterDraftEditor,
  type Proposal,
  type ProposalChange,
  type CharacterPreview,
} from "../character/draft-editor";
import { ScenarioCatalog } from "./catalog";
import { WorkshopReviewQueue } from "../character/review-queue";
import { LiveTransport } from "../play/live";
import type { Campaign } from "../play/transport";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "../components/ui/button";
import { ConfirmDialog } from "../components/ui/confirm";
import { ProviderBanner } from "../components/availability";
import { providerReason } from "../presentation/availability";
import { TechnicalDetails } from "../components/technical-details";
import { NetworkPlayTransport } from "../api/play-transport";
import {
  campaignPhaseLabel,
  difficulties,
  difficultyLabel,
  humanize,
  lifecycleOperationLabel,
  poolLabel,
} from "../presentation/labels";
import type { PlayTransport } from "../play/transport";
import {
  SetupClient,
  type Brief,
  type Graph,
  type Lobby,
  type RulesProfile,
} from "./client";
const blank: Brief = {
  premise: "",
  genre: "Fantasy",
  tone: "Adventurous",
  duration_minutes: 90,
  difficulty: "standard",
  restrictions: [],
};
const sameBrief = (left: Brief, right: Brief) =>
  left.premise === right.premise &&
  left.genre === right.genre &&
  left.tone === right.tone &&
  left.duration_minutes === right.duration_minutes &&
  left.difficulty === right.difficulty &&
  left.restrictions.length === right.restrictions.length &&
  left.restrictions.every(
    (value, index) => value === right.restrictions[index],
  );
/** The lobby surfaces, one per tab of the shell above it. */
export type SetupMode = "new" | "continue" | "join" | "scenarios";
/**
 * The authenticated setup session. The caller keeps it so the setup shell can
 * unmount while a campaign is being played without asking for the token again.
 */
export interface SetupSession {
  token: string;
  principal: string;
  generationAvailable: boolean;
  legacyAvailable: boolean;
}
/**
 * One step of setup is shown at a time; a draft is never a stack of forms.
 *
 * The numbered steps are the four that author a setup, in the order the flow
 * actually walks them. Party assignment happens after the draft exists, so it
 * is the screen that follows creation rather than a step of the wizard nobody
 * could reach going forward (#259, #260).
 */
const steps = ["Concept", "Adventure", "Rules", "Ready"] as const;
type Step = (typeof steps)[number];
type Screen = Step | "Party";
/**
 * The lifecycle operations a host is offered in each phase, and the ones that
 * cannot be taken back. `complete` ends the campaign for everyone with no way
 * back to play, so it asks before it acts; the reversible operations stay a
 * single press and offer their own way back (#163).
 */
const lifecycleActions: Record<Lobby["phase"], readonly string[]> = {
  draft: [],
  ready: ["activate"],
  active: ["pause", "complete"],
  paused: ["resume"],
  completed: ["archive"],
  archived: ["unarchive"],
};
const irreversible = new Set(["complete"]);
/** A host resumes an editable draft at its party; anyone else at readiness. */
const landing = (value: Lobby, principal: string): Screen =>
  value.host_id === principal &&
  (value.phase === "draft" || value.phase === "ready")
    ? "Party"
    : "Ready";
export function SetupLobby({
  onOpen,
  mode = "new",
  initialSession,
  initialCampaignId,
  onSession,
  onMode,
}: {
  mode?: SetupMode;
  onOpen: (transport: PlayTransport) => void;
  initialSession?: SetupSession | undefined;
  initialCampaignId?: string | undefined;
  onSession?: ((value: SetupSession | undefined) => void) | undefined;
  /** Lets a surface hand the shell back to another one, such as the wizard. */
  onMode?: ((value: SetupMode) => void) | undefined;
}) {
  const [secret, setSecret] = useState("");
  const [session, setSession] = useState(initialSession);
  const [games, setGames] = useState<Campaign[]>([]);
  const [lobbies, setLobbies] = useState<Lobby[]>([]),
    [lobby, setLobby] = useState<Lobby>();
  const [templates, setTemplates] = useState<Graph[]>([]),
    [graph, setGraph] = useState<Graph | null>(null);
  const [profiles, setProfiles] = useState<RulesProfile[]>([]),
    [profile, setProfile] = useState("");
  const [brief, setBrief] = useState(blank),
    [invite, setInvite] = useState("");
  // Once a concept came from a person (or was explicitly accepted from an
  // adventure), selecting another adventure must not silently replace it.
  const [conceptProtected, setConceptProtected] = useState(false),
    [conceptBackup, setConceptBackup] = useState<Brief | null>(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [step, setStep] = useState<Screen>("Concept");
  /** The irreversible lifecycle operation waiting on its confirmation, if any. */
  const [confirming, setConfirming] = useState<string>();
  /** Set by a pause this session, so its undo is offered where it was taken. */
  const [undoPause, setUndoPause] = useState(false);
  const client = useMemo(
    () =>
      session ? new SetupClient(session.token, session.principal) : undefined,
    [session],
  );
  const previewPartyCharacter = useCallback(
    async (proposal: Proposal, signal: AbortSignal) => {
      if (!client || !lobby)
        throw new Error("Save the game before editing its party.");
      return client.request<CharacterPreview>(
        `/${encodeURIComponent(lobby.id)}/character-preview`,
        { proposal },
        signal,
      );
    },
    [client, lobby],
  );
  const remember = (value: SetupSession | undefined) => {
    setSession(value);
    onSession?.(value);
  };
  const open = (value: Lobby) => {
    if (!session) return;
    onOpen(
      new NetworkPlayTransport({
        origin: location.origin,
        credential: session.token,
        principalId: session.principal,
        initialCampaignId: value.id,
        engineControls: value.engine_controls ?? false,
      }),
    );
  };
  const choose = (value: Lobby) => {
    setUndoPause(false);
    setLobby(value);
    setBrief(value.brief);
    setGraph(value.graph);
    setConceptProtected(true);
    setConceptBackup(null);
  };
  const restart = () => {
    setLobby(undefined);
    setGraph(null);
    setBrief(blank);
    setConceptProtected(false);
    setConceptBackup(null);
    setStep("Concept");
  };
  const editBrief = (value: Brief) => {
    setBrief(value);
    setConceptProtected(true);
  };
  const restoreConcept = conceptBackup && (
    <div>
      <p role="status">The adventure concept is in use.</p>
      <Button
        type="button"
        variant="outline"
        onClick={() => {
          setBrief(conceptBackup);
          setConceptBackup(null);
          setConceptProtected(true);
        }}
      >
        Restore previous concept
      </Button>
    </div>
  );
  const run = async (work: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };
  const command = async (
    operation: string,
    extra: object = {},
    suffix = "",
    // Starting or resuming a session enters play, because that is what the
    // host asked for. Undoing a pause is not that ask: it puts the campaign
    // back the way it was and leaves the panel where it stands.
    { enter = true }: { enter?: boolean } = {},
  ) => {
    if (!client || !lobby) return;
    const result = await client.write(`/${lobby.id}${suffix}`, {
      id: crypto.randomUUID(),
      expected_revision: lobby.revision,
      operation,
      ...extra,
    });
    choose(result);
    if (enter && (operation === "activate" || operation === "resume"))
      open(result);
  };
  // A restored session reloads its own games, drafts and catalogs.
  useEffect(() => {
    if (!client || !session) return;
    let cancelled = false;
    void (async () => {
      setBusy(true);
      setError("");
      try {
        const saved = await new NetworkPlayTransport({
          origin: location.origin,
          credential: session.token,
          principalId: session.principal,
        }).listCampaigns(new AbortController().signal);
        const values = await client.request<Lobby[]>("");
        const available = await client.request<Graph[]>("/templates");
        const registered = await client.request<RulesProfile[]>("/profiles");
        if (cancelled) return;
        setGames(saved);
        setLobbies(values);
        setTemplates(available);
        setProfiles(registered);
        // Returning from play reopens the campaign that was being played.
        if (values.some((value) => value.id === initialCampaignId)) {
          const current = await client.request<Lobby>(`/${initialCampaignId}`);
          if (cancelled) return;
          setLobby(current);
          setBrief(current.brief);
          setGraph(current.graph);
          setConceptProtected(true);
          setConceptBackup(null);
          setStep(landing(current, session.principal));
        }
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Request failed");
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, session, initialCampaignId]);
  const host = !!session && lobby?.host_id === session.principal;
  const editable = !lobby || lobby.phase === "draft" || lobby.phase === "ready";
  const canEdit = editable && (!lobby || host) && !lobby?.scenario_pinned;
  /**
   * A brief a draft can be created from. The concept step asks for these three
   * fields, and choosing an authored adventure fills them in, so either entry
   * into the flow unlocks its review step.
   */
  const complete =
    !!brief.premise.trim() && !!brief.genre.trim() && !!brief.tone.trim();
  /**
   * Only the last step is gated, and only on the thing it reviews, so no chip is
   * ever offered while an earlier one is closed (#260).
   */
  const reachable = (value: Step) =>
    value === "Ready" ? !!lobby || complete : true;
  /** Back and Next walk the steps that can actually be opened right now. */
  const sequence = steps.filter(reachable);
  const at = step === "Party" ? -1 : sequence.indexOf(step);
  /** The party follows the wizard, so its way back is the step it followed. */
  const previous =
    step === "Party" ? "Ready" : at > 0 ? sequence[at - 1] : undefined;
  const following = step === "Party" ? undefined : sequence[at + 1];
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!client) return;
    // A draft is created from the review step alone, never by a stray submit
    // of a step that is still collecting the brief.
    if (!lobby && step !== "Ready") return;
    void run(async () => {
      if (!lobby) {
        const selected = profiles.find(
          (p) => `${p.id}@${p.version}` === profile,
        );
        const result = await client.write("", {
          id: crypto.randomUUID(),
          brief,
          graph: graph ? { ...graph, brief } : null,
          ...(selected
            ? { rules_profile: { id: selected.id, version: selected.version } }
            : {}),
        });
        choose(result);
        setLobbies([...lobbies, result]);
        // Creating the draft unlocks the party, which is the work left to do.
        setStep("Party");
      } else
        await command("edit", {
          brief,
          graph: graph ? { ...graph, brief } : null,
        });
    });
  };
  /**
   * The service sends assignable character names alongside the seats, so every
   * player reads a name where the host reads one; only the host sees the graph
   * those names live in. An unrecognized id still reads as words, never raw.
   */
  const characterName = (actorId: string) =>
    lobby?.party?.find((a) => a.actor_id === actorId)?.name ??
    humanize(actorId);
  /** The party roster; only the party step offers its assignment controls. */
  const players = (assignable: boolean) =>
    lobby && (
      <ul className="seat-list" aria-label="Players">
        {lobby.seats.map((seat) => (
          <li key={seat.principal_id} className="seat-row">
            <span className="seat-player">{seat.principal_id}</span>
            <span className="seat-character">
              {seat.actor_ids.map(characterName).join(", ") ||
                "No character assigned"}
            </span>
            <span className="seat-status">
              {!seat.joined ? "Invited" : seat.ready ? "Ready" : "Not ready"}
            </span>
            {assignable && host && editable && seat.joined && lobby.graph && (
              <label>
                Assign character to {seat.principal_id}
                <select
                  aria-label={`Assign character to ${seat.principal_id}`}
                  value={seat.actor_ids[0] ?? ""}
                  disabled={busy || client?.hasPending}
                  onChange={(e) =>
                    void run(() =>
                      command("assign", {
                        principal_id: seat.principal_id,
                        actor_ids: e.target.value ? [e.target.value] : [],
                      }),
                    )
                  }
                >
                  <option value="">Choose character</option>
                  {lobby.graph.actors
                    .filter(
                      (a) => !lobby.graph!.npc_actor_ids.includes(a.actor_id),
                    )
                    .map((a) => (
                      <option key={a.actor_id} value={a.actor_id}>
                        {characterName(a.actor_id)}
                      </option>
                    ))}
                </select>
              </label>
            )}
          </li>
        ))}
      </ul>
    );
  /**
   * Editing steps offer saving once there is a draft to save into. Before
   * that, a step is filled in and left with Next; the review step creates.
   */
  const submit = lobby ? (
    <Button disabled={busy || client?.hasPending}>Save setup draft</Button>
  ) : null;
  /** Signed-in upkeep, shared by every surface of the lobby. */
  const account =
    session && client ? (
      <p className="lobby-account">
        Signed in as {session.principal}
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              if (client.hasPending) {
                const recovered = await client.retry();
                choose(recovered);
                if (recovered.phase === "active") open(recovered);
              }
              const values = await client.request<Lobby[]>("");
              setLobbies(values);
              if (lobby) choose(await client.request<Lobby>(`/${lobby.id}`));
            })
          }
        >
          Refresh this list
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            remember(undefined);
            setSecret("");
            setGames([]);
            setTemplates([]);
            setProfiles([]);
            setLobbies([]);
            restart();
          }}
        >
          Sign out
        </Button>
      </p>
    ) : null;
  /** Back and Next make the sequence the step chips describe an actual one. */
  const walk = (
    <nav className="step-nav" aria-label="Setup step navigation">
      <Button
        type="button"
        variant="outline"
        disabled={!previous}
        onClick={() => previous && setStep(previous)}
      >
        Back
      </Button>
      {step !== "Ready" && step !== "Party" && (
        <Button
          type="button"
          disabled={!following}
          onClick={() => following && setStep(following)}
        >
          {following ? `Next: ${following}` : "Next"}
        </Button>
      )}
      {!following && step !== "Ready" && step !== "Party" && (
        <p>
          Write a premise on the concept step, or choose an authored adventure,
          to review and create the draft.
        </p>
      )}
    </nav>
  );
  return (
    <section className="scene-card setup-lobby" aria-label="New game and lobby">
      {/* The mode tab above names this panel; repeating it as a heading made
          selecting a mode look as though nothing had happened (#200). */}
      <h2 className="visually-hidden">Game setup</h2>
      {!session || !client ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const auth = await new SetupClient(secret).request<{
                principal_id: string;
                generation_available: boolean;
                legacy_available: boolean;
              }>("/session");
              remember({
                token: secret,
                principal: auth.principal_id,
                generationAvailable: auth.generation_available,
                legacyAvailable: auth.legacy_available,
              });
            });
          }}
        >
          <p>Sign in with your own access token. No campaign ID is needed.</p>
          <label>
            Access token
            <input
              required
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </label>
          <Button disabled={busy}>Sign in</Button>
        </form>
      ) : mode === "scenarios" ? (
        /* Scenario authoring is a library, not a step of setting up a game: it
           keeps its own surface, where a document format and a publish history
           are the subject rather than an aside (#261). */
        <>
          <h3 className="lobby-heading">Scenario library</h3>
          <p>
            Write, import and publish scenarios here. A published scenario
            becomes an adventure you can choose under <strong>New game</strong>.
          </p>
          {account}
          {!session.generationAvailable && <ProviderBanner />}
          <ScenarioCatalog
            token={session.token}
            principal={session.principal}
            generationAvailable={session.generationAvailable}
            concept={brief}
            onCreate={(value) => {
              choose(value);
              setLobbies([...lobbies, value]);
              setStep("Party");
              onMode?.("new");
            }}
          />
        </>
      ) : (
        <>
          {/* The list this page exists to show comes first; keeping the shell's
              own upkeep above it put maintenance ahead of content (#204). */}
          <h3 className="lobby-heading">
            {mode === "join"
              ? "Invitations and games"
              : "Your saved games and unfinished drafts"}
          </h3>
          {mode === "join" && (
            <p>
              Ask the host to invite your player name, then refresh this list to
              accept your invitation.
            </p>
          )}
          {!lobbies.length && !games.length && !busy && (
            <p>
              No saved games yet. Start one below, and it appears here for every
              later visit.
            </p>
          )}
          <ul>
            {lobbies.map((value) => (
              <li key={value.id}>
                <Button
                  data-campaign-id={value.id}
                  variant="outline"
                  disabled={busy || client.hasPending}
                  onClick={() =>
                    void run(async () => {
                      const saved = await client.request<Lobby>(`/${value.id}`);
                      choose(saved);
                      setStep(landing(saved, session.principal));
                      if (saved.phase === "active") open(saved);
                    })
                  }
                >
                  {value.title} · {campaignPhaseLabel(value.phase)}
                </Button>
              </li>
            ))}
          </ul>
          <ul>
            {games
              .filter((game) => !lobbies.some((value) => value.id === game.id))
              .map((game) => (
                <li key={game.id}>
                  <Button
                    data-resume-id={game.id}
                    onClick={() =>
                      onOpen(
                        session.legacyAvailable
                          ? new LiveTransport(
                              session.principal,
                              game.id,
                              session.token,
                            )
                          : new NetworkPlayTransport({
                              origin: location.origin,
                              credential: session.token,
                              principalId: session.principal,
                              initialCampaignId: game.id,
                            }),
                      )
                    }
                  >
                    Continue {game.name}
                  </Button>
                  {session.legacyAvailable && game.membership.role === "gm" && (
                    <WorkshopReviewQueue
                      key={`${session.principal}:${game.id}`}
                      campaignId={game.id}
                      principal={session.principal}
                      token={session.token}
                    />
                  )}
                </li>
              ))}
          </ul>
          {/* Keeping the list current and leaving setup are upkeep, so they
              read as upkeep: secondary, in user words, below the list (#204). */}
          {account}
          {!session.generationAvailable && <ProviderBanner />}
          <div className="context-actions">
            <Button
              type="button"
              disabled={busy || client.hasPending}
              onClick={restart}
            >
              Start a new game
            </Button>
          </div>
          {lobby ? (
            <p role="status">
              {lobby.title} · {campaignPhaseLabel(lobby.phase)} · revision{" "}
              {lobby.revision}
            </p>
          ) : (
            <p>Create a game, or open an invitation above.</p>
          )}
          {/* The line names the step; the chips below it are the same five
              steps, shown as numbered dots where a labelled row cannot fit on
              one line (#206). Every chip keeps its step name as its accessible
              label at every width. */}
          <nav className="setup-steps" aria-label="Setup steps">
            <p className="eyebrow">
              {step === "Party"
                ? "Party · after the draft is created"
                : `Step ${steps.indexOf(step) + 1} of ${steps.length}: ${step}`}
            </p>
            <ol>
              {steps.map((value, index) => (
                <li key={value}>
                  <Button
                    type="button"
                    variant={value === step ? "default" : "outline"}
                    aria-current={value === step ? "step" : undefined}
                    disabled={!reachable(value)}
                    onClick={() => setStep(value)}
                  >
                    <span className="step-index" aria-hidden="true">
                      {index + 1}
                    </span>
                    <span className="step-name">{value}</span>
                  </Button>
                </li>
              ))}
            </ol>
            {/* Not a numbered step: the party exists only once the service
                holds a draft, so it is the screen that follows the wizard
                rather than a chip nobody could walk forward into (#259). */}
            {lobby && (
              <p className="setup-next-screen">
                <Button
                  type="button"
                  variant={step === "Party" ? "default" : "outline"}
                  aria-current={step === "Party" ? "page" : undefined}
                  onClick={() => setStep("Party")}
                >
                  Party
                </Button>
              </p>
            )}
          </nav>
          {step === "Concept" &&
            (canEdit ? (
              <form onSubmit={save}>
                <label>
                  Premise
                  <textarea
                    required
                    value={brief.premise}
                    maxLength={4000}
                    onChange={(e) =>
                      editBrief({ ...brief, premise: e.target.value })
                    }
                  />
                </label>
                <label>
                  Genre
                  <input
                    required
                    value={brief.genre}
                    onChange={(e) =>
                      editBrief({ ...brief, genre: e.target.value })
                    }
                  />
                </label>
                <label>
                  Tone
                  <input
                    required
                    value={brief.tone}
                    onChange={(e) =>
                      editBrief({ ...brief, tone: e.target.value })
                    }
                  />
                </label>
                <label>
                  Duration (minutes)
                  <input
                    type="number"
                    min={10}
                    max={10000}
                    value={brief.duration_minutes}
                    onChange={(e) =>
                      editBrief({
                        ...brief,
                        duration_minutes: Number(e.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  Difficulty
                  <select
                    value={brief.difficulty}
                    onChange={(e) =>
                      editBrief({
                        ...brief,
                        difficulty: e.target.value as Brief["difficulty"],
                      })
                    }
                  >
                    {difficulties.map((d) => (
                      <option key={d} value={d}>
                        {difficultyLabel(d)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Boundaries and restrictions
                  <textarea
                    value={brief.restrictions.join("\n")}
                    onChange={(e) =>
                      editBrief({
                        ...brief,
                        restrictions: e.target.value.split("\n"),
                      })
                    }
                  />
                </label>
                {restoreConcept}
                {submit}
                {lobby && session.generationAvailable && (
                  <Button
                    type="button"
                    disabled={busy || client.hasPending}
                    onClick={() =>
                      void run(() => command("edit", {}, "/generate"))
                    }
                  >
                    Generate from saved brief
                  </Button>
                )}
              </form>
            ) : (
              <p>
                This game’s premise is fixed. Only its host can edit a draft,
                and a started or pinned game keeps the concept it was created
                with.
              </p>
            ))}
          {/* The step asks one question and answers it: which adventure. Writing
              scenarios, importing documents and publishing revisions are a
              library, and the library is its own surface (#261). */}
          {step === "Adventure" && (
            <>
              {canEdit ? (
                <form onSubmit={save}>
                  <label>
                    Adventure and starting party
                    <select
                      value={graph?.id ?? ""}
                      onChange={(e) => {
                        const selected =
                          templates.find((t) => t.id === e.target.value) ??
                          null;
                        setGraph(selected);
                        // A pristine setup may be seeded from its first
                        // adventure. Once anything has supplied a concept,
                        // changing adventures preserves it until the user
                        // explicitly chooses the replacement below.
                        if (selected && !conceptProtected) {
                          setBrief(selected.brief);
                          setConceptProtected(true);
                        }
                      }}
                    >
                      <option value="">Choose an adventure</option>
                      {templates.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  {graph && !sameBrief(brief, graph.brief) && (
                    <div>
                      <p role="status">
                        Your Concept answers were kept. This adventure has a
                        different suggested concept.
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => {
                          setConceptBackup((value) => value ?? brief);
                          setBrief(graph.brief);
                          setConceptProtected(true);
                        }}
                      >
                        Use adventure concept
                      </Button>
                    </div>
                  )}
                  {restoreConcept}
                  {!templates.length && (
                    <p>
                      No authored adventures are installed. Save your premise,
                      then generate an adventure if a provider is configured.
                    </p>
                  )}
                  {submit}
                </form>
              ) : (
                <p>
                  This game’s adventure is fixed. Its host chose the scenario
                  and starting party when the draft was created.
                </p>
              )}
              {!lobby && onMode && (
                <p className="setup-secondary">
                  Writing one of your own, starting from a template or importing
                  a scenario document happens in the scenario library.
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => onMode("scenarios")}
                  >
                    Open the scenario library
                  </Button>
                </p>
              )}
            </>
          )}
          {step === "Rules" && (
            <>
              {canEdit && (
                <form onSubmit={save}>
                  {!lobby && profiles.length > 0 ? (
                    <>
                      <label>
                        Rules profile
                        <select
                          value={profile}
                          onChange={(e) => setProfile(e.target.value)}
                        >
                          <option value="">This game’s default rules</option>
                          {profiles.map((p) => (
                            <option
                              key={`${p.id}@${p.version}`}
                              value={`${p.id}@${p.version}`}
                              disabled={!p.supported}
                            >
                              {p.title} (v{p.version})
                              {p.supported ? "" : " · Not yet supported"}
                            </option>
                          ))}
                        </select>
                      </label>
                      {profiles.some((p) => !p.supported) && (
                        <>
                          <p>
                            Rule sets marked “Not yet supported” cannot be
                            chosen yet. Every other choice plays in full.
                          </p>
                          {/* Which capabilities are missing is maintainers' business, not a player's (#162). */}
                          <TechnicalDetails
                            entries={profiles
                              .filter((p) => !p.supported)
                              .map((p) => ({
                                label: `${p.title} (v${p.version}) unverified capabilities:`,
                                value:
                                  p.unverified_capabilities.join(", ") ||
                                  "none listed",
                              }))}
                          />
                        </>
                      )}
                    </>
                  ) : (
                    <p>
                      {lobby
                        ? "This game keeps the rules pinned when its draft was created."
                        : "This game uses its default rules. There is nothing to choose here."}
                    </p>
                  )}
                  {submit}
                </form>
              )}
              {lobby && (
                <details>
                  <summary>
                    Campaign rules
                    {lobby.rules_profile
                      ? `: ${lobby.rules_profile.title} (v${lobby.rules_profile.version})`
                      : ""}
                  </summary>
                  <p>
                    Saved games keep their exact rules pins. Changing profiles
                    is an explicit host migration of a paused game.
                  </p>
                  <pre>{JSON.stringify(lobby.rules, null, 2)}</pre>
                </details>
              )}
            </>
          )}
          {step === "Party" && (
            <>
              {canEdit && (
                <form onSubmit={save}>
                  {/* Two full character sheets on one page is four screens of
                      form; each opens on its own, and the first is the one
                      already open (#270). */}
                  {graph?.actors
                    .filter((a) => !graph.npc_actor_ids.includes(a.actor_id))
                    .map((actor, index) => (
                      <details
                        key={actor.actor_id}
                        className="party-character"
                        open={index === 0}
                      >
                        <summary>
                          Character {characterName(actor.actor_id)}
                        </summary>
                        <CharacterDraftEditor
                          proposal={actor.proposal}
                          preview={previewPartyCharacter}
                          disabled={busy || !!client?.hasPending}
                          templates={templates.flatMap((template) =>
                            template.actors
                              .filter(
                                (a) =>
                                  !template.npc_actor_ids.includes(a.actor_id),
                              )
                              .map((a) => ({
                                title: `${a.proposal.draft.name} · ${template.title}`,
                                proposal: a.proposal,
                              })),
                          )}
                          onChange={(change: ProposalChange) =>
                            setGraph((current) => {
                              if (!current) return current;
                              return {
                                ...current,
                                actors: current.actors.map((currentActor) => {
                                  if (currentActor.actor_id !== actor.actor_id)
                                    return currentActor;
                                  const proposal =
                                    typeof change === "function"
                                      ? change(currentActor.proposal)
                                      : change;
                                  return { ...currentActor, proposal };
                                }),
                              };
                            })
                          }
                        />
                      </details>
                    ))}
                  <p>
                    Characters and equipment are checked against the server’s
                    pinned rules before readiness. Saving edits clears
                    assignments and readiness.
                  </p>
                  {submit}
                </form>
              )}
              {players(true)}
              {lobby && editable && host && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(() => command("invite", { principal_id: invite }));
                  }}
                >
                  <label>
                    Invite player ID
                    <input
                      required
                      value={invite}
                      onChange={(e) => setInvite(e.target.value)}
                    />
                  </label>
                  <Button disabled={busy || client.hasPending}>
                    Invite player
                  </Button>
                </form>
              )}
            </>
          )}
          {step === "Ready" && lobby && (
            <>
              {players(false)}
              {lobby.adventures?.map((ending) => (
                <article
                  key={ending.adventure_id}
                  aria-label="Adventure conclusion"
                >
                  <h3>
                    {ending.title} · {ending.outcome}
                  </h3>
                  <p>
                    Adventure ended at shared time {ending.at}. The campaign can
                    continue after any outcome.
                  </p>
                  <ul>
                    {ending.evidence.map((e) => (
                      <li key={e.id}>
                        {e.title}: {e.satisfied ? "Achieved" : "Unfulfilled"}
                      </li>
                    ))}
                  </ul>
                  <h4>Discoveries</h4>
                  <ul>
                    {ending.discoveries.map((f) => (
                      <li key={f.id}>
                        {f.predicate}: {f.value}
                      </li>
                    ))}
                  </ul>
                  <h4>Lasting commitments</h4>
                  <ul>
                    {ending.commitments.map((c) => (
                      <li key={c.id}>
                        {c.description} · {c.status}
                      </li>
                    ))}
                  </ul>
                  <p>
                    Recorded casualties:{" "}
                    {ending.casualties.map(characterName).join(", ") ||
                      "None visible"}
                  </p>
                  <h4>Settled rewards and advancement</h4>
                  <ul>
                    {ending.rewards.map((r) => (
                      <li key={r.id}>
                        {r.points} points{r.item_id ? ` · ${r.item_id}` : ""}
                      </li>
                    ))}
                  </ul>
                  <ul>
                    {ending.advancement.map((e) => (
                      <li key={e.id}>
                        {e.kind}: {e.points} · {e.reason}
                      </li>
                    ))}
                  </ul>
                  <h4>Recovery status</h4>
                  <ul>
                    {ending.pools.map((p) => (
                      <li key={p.id}>
                        {poolLabel(p.id, characterName)} {p.current}/{p.maximum}
                      </li>
                    ))}
                  </ul>
                  <p>
                    Continuing preserves injuries and equipment. Use authored
                    downtime and advancement actions in play.
                  </p>
                </article>
              ))}
              {lobby.phase === "completed" && host && (
                <div>
                  <h3>Next adventure</h3>
                  <label>
                    Authored next adventure
                    <select
                      value={graph?.id ?? ""}
                      onChange={(e) =>
                        setGraph(
                          templates.find((t) => t.id === e.target.value) ??
                            null,
                        )
                      }
                    >
                      <option value="">Choose a successor</option>
                      {templates
                        .filter(
                          (t) =>
                            !lobby.adventures?.some(
                              (a) => a.adventure_id === t.id,
                            ),
                        )
                        .map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.title}
                          </option>
                        ))}
                    </select>
                  </label>
                  <Button
                    disabled={!graph || busy || client.hasPending}
                    onClick={() =>
                      void run(() => command("preview", { graph }))
                    }
                  >
                    Save next-adventure preview
                  </Button>
                  <p>
                    Generate a successor from the saved party and lasting world
                    state. The saved preview survives reloads.
                  </p>
                  <Button
                    disabled={
                      !session.generationAvailable || busy || client.hasPending
                    }
                    title={
                      session.generationAvailable
                        ? undefined
                        : providerReason.creation
                    }
                    onClick={() =>
                      void run(() => command("preview", {}, "/generate"))
                    }
                  >
                    Generate next-adventure preview
                  </Button>
                  {!session.generationAvailable && (
                    <p>{providerReason.creation}</p>
                  )}
                </div>
              )}
              {lobby.next_adventure && (
                <article aria-label="Next adventure preview">
                  <h3>{lobby.next_adventure.title}</h3>
                  <p>{lobby.next_adventure.opening_action}</p>
                </article>
              )}
              {lobby.phase === "archived" && (
                <p>
                  Archived games are read-only. Unarchive returns to the
                  conclusion, where you can continue.
                </p>
              )}
              {editable && (
                <div className="context-actions">
                  <Button
                    disabled={busy || client.hasPending}
                    onClick={() => void run(() => command("join"))}
                  >
                    Accept invitation
                  </Button>
                  <Button
                    disabled={busy || client.hasPending}
                    onClick={() => void run(() => command("ready"))}
                  >
                    Validate and mark ready
                  </Button>
                </div>
              )}
              {host && lifecycleActions[lobby.phase].length > 0 && (
                <section
                  className="lifecycle-actions"
                  aria-label="Campaign lifecycle"
                >
                  <div className="context-actions">
                    {[
                      ...lifecycleActions[lobby.phase],
                      ...(lobby.phase === "completed" && lobby.next_adventure
                        ? ["continue"]
                        : []),
                    ].map((operation) => (
                      <Button
                        key={operation}
                        variant={
                          irreversible.has(operation) ? "danger" : "default"
                        }
                        disabled={busy || client.hasPending}
                        onClick={() => {
                          if (irreversible.has(operation)) {
                            setConfirming(operation);
                            return;
                          }
                          void run(async () => {
                            await command(operation);
                            if (operation === "pause") setUndoPause(true);
                          });
                        }}
                      >
                        {lifecycleOperationLabel(operation)}
                      </Button>
                    ))}
                  </div>
                  {undoPause && lobby.phase === "paused" && (
                    <div className="lifecycle-undo" role="status">
                      <p>
                        Paused “{lobby.title}”. Nobody can act in this campaign
                        until it resumes.
                      </p>
                      <Button
                        variant="outline"
                        disabled={busy || client.hasPending}
                        onClick={() =>
                          void run(() =>
                            command("resume", {}, "", { enter: false }),
                          )
                        }
                      >
                        Undo pause
                      </Button>
                    </div>
                  )}
                </section>
              )}
              <div className="context-actions">
                {lobby.phase === "active" && (
                  <Button disabled={busy} onClick={() => open(lobby)}>
                    Open playing scene
                  </Button>
                )}
                <Button variant="outline" disabled={busy} onClick={restart}>
                  Create another game
                </Button>
              </div>
              <ConfirmDialog
                open={!!confirming}
                title={`End “${lobby.title}”?`}
                description="Ending this campaign finishes it for every player at the scene they are in. It cannot be returned to play: a finished campaign can only be archived, or continued as a new adventure. Pause the session instead if the table is stopping for now."
                confirmLabel="End this campaign"
                cancelLabel="Keep playing"
                busy={busy || client.hasPending}
                onConfirm={() => {
                  const operation = confirming;
                  setConfirming(undefined);
                  if (operation) void run(() => command(operation));
                }}
                onCancel={() => setConfirming(undefined)}
              />
            </>
          )}
          {step === "Ready" && !lobby && (
            <form onSubmit={save} aria-label="Review and create">
              <h3>Review this setup</h3>
              <dl className="setup-review">
                <div>
                  <dt>Concept</dt>
                  <dd>{brief.premise || "No premise written yet"}</dd>
                </div>
                <div>
                  <dt>Style</dt>
                  <dd>
                    {brief.genre} · {brief.tone} · {brief.duration_minutes}{" "}
                    minutes · {difficultyLabel(brief.difficulty)}
                  </dd>
                </div>
                <div>
                  <dt>Adventure</dt>
                  <dd>
                    {graph?.title ??
                      "No authored adventure; the premise alone starts the draft"}
                  </dd>
                </div>
                <div>
                  <dt>Rules</dt>
                  <dd>
                    {profiles.find((p) => `${p.id}@${p.version}` === profile)
                      ?.title ?? "This game’s default rules"}
                  </dd>
                </div>
              </dl>
              <Button disabled={busy || !complete || client.hasPending}>
                Create game draft
              </Button>
              <p>
                Creating the draft opens its party, where characters are
                assigned and players invited. Nothing is published or started.
              </p>
            </form>
          )}
          {walk}
          {client.hasPending && (
            <Button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const recovered = await client.retry();
                  choose(recovered);
                  if (recovered.phase === "active") open(recovered);
                })
              }
            >
              Retry original setup request
            </Button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
