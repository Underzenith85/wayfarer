import {
  CharacterDraftEditor,
  type Proposal,
  type CharacterPreview,
} from "../character/draft-editor";
import { ScenarioCatalog } from "./catalog";
import { WorkshopReviewQueue } from "../character/review-queue";
import { LiveTransport } from "../play/live";
import type { Campaign } from "../play/transport";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "../components/ui/button";
import { ProviderBanner } from "../components/availability";
import { providerReason } from "../presentation/availability";
import { TechnicalDetails } from "../components/technical-details";
import { NetworkPlayTransport } from "../api/play-transport";
import {
  campaignPhaseLabel,
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
/** One step of setup is shown at a time; a draft is never a stack of forms. */
const steps = ["Concept", "Adventure", "Rules", "Party", "Ready"] as const;
type Step = (typeof steps)[number];
/** A host resumes an editable draft at its party; anyone else at readiness. */
const landing = (value: Lobby, principal: string): Step =>
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
}: {
  mode?: "new" | "continue" | "join";
  onOpen: (transport: PlayTransport) => void;
  initialSession?: SetupSession | undefined;
  initialCampaignId?: string | undefined;
  onSession?: ((value: SetupSession | undefined) => void) | undefined;
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
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [step, setStep] = useState<Step>("Concept");
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
    setLobby(value);
    setBrief(value.brief);
    setGraph(value.graph);
  };
  const restart = () => {
    setLobby(undefined);
    setGraph(null);
    setBrief(blank);
    setStep("Concept");
  };
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
  ) => {
    if (!client || !lobby) return;
    const result = await client.write(`/${lobby.id}${suffix}`, {
      id: crypto.randomUUID(),
      expected_revision: lobby.revision,
      operation,
      ...extra,
    });
    choose(result);
    if (operation === "activate" || operation === "resume") open(result);
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
   * The party is only assignable once the service holds a draft; review is
   * reachable earlier because creating the draft is its own step's action.
   */
  const reachable = (value: Step) =>
    value === "Party"
      ? !!lobby
      : value === "Ready"
        ? !!lobby || complete
        : true;
  /** Back and Next walk the steps that can actually be opened right now. */
  const sequence = steps.filter(reachable);
  const at = sequence.indexOf(step);
  const previous = at > 0 ? sequence[at - 1] : undefined;
  const following = sequence[at + 1];
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
      {step !== "Ready" && (
        <Button
          type="button"
          disabled={!following}
          onClick={() => following && setStep(following)}
        >
          {following ? `Next: ${following}` : "Next"}
        </Button>
      )}
      {!following && step !== "Ready" && (
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
                  if (lobby)
                    choose(await client.request<Lobby>(`/${lobby.id}`));
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
              Step {steps.indexOf(step) + 1} of {steps.length}: {step}
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
                      setBrief({ ...brief, premise: e.target.value })
                    }
                  />
                </label>
                <label>
                  Genre
                  <input
                    required
                    value={brief.genre}
                    onChange={(e) =>
                      setBrief({ ...brief, genre: e.target.value })
                    }
                  />
                </label>
                <label>
                  Tone
                  <input
                    required
                    value={brief.tone}
                    onChange={(e) =>
                      setBrief({ ...brief, tone: e.target.value })
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
                      setBrief({
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
                      setBrief({
                        ...brief,
                        difficulty: e.target.value as Brief["difficulty"],
                      })
                    }
                  >
                    {["gentle", "standard", "hard"].map((d) => (
                      <option key={d}>{d}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Boundaries and restrictions
                  <textarea
                    value={brief.restrictions.join("\n")}
                    onChange={(e) =>
                      setBrief({
                        ...brief,
                        restrictions: e.target.value.split("\n"),
                      })
                    }
                  />
                </label>
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
                        if (selected) setBrief(selected.brief);
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
              {!lobby && (
                <ScenarioCatalog
                  token={session.token}
                  principal={session.principal}
                  generationAvailable={session.generationAvailable}
                  onCreate={(value) => {
                    choose(value);
                    setLobbies([...lobbies, value]);
                    setStep("Party");
                  }}
                />
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
                  {graph?.actors
                    .filter((a) => !graph.npc_actor_ids.includes(a.actor_id))
                    .map((actor) => (
                      <fieldset key={actor.actor_id}>
                        <legend>
                          Character {characterName(actor.actor_id)}
                        </legend>
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
                          onChange={(proposal) =>
                            setGraph({
                              ...graph,
                              actors: graph.actors.map((a) =>
                                a === actor ? { ...a, proposal } : a,
                              ),
                            })
                          }
                        />
                      </fieldset>
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
              <div className="context-actions">
                {host &&
                  {
                    ready: ["activate"],
                    active: ["pause", "complete"],
                    paused: ["resume"],
                    completed: [
                      "archive",
                      ...(lobby.next_adventure ? ["continue"] : []),
                    ],
                    draft: [],
                    archived: ["unarchive"],
                  }[lobby.phase].map((operation) => (
                    <Button
                      key={operation}
                      disabled={busy || client.hasPending}
                      onClick={() => void run(() => command(operation))}
                    >
                      {lifecycleOperationLabel(operation)}
                    </Button>
                  ))}
                {lobby.phase === "active" && (
                  <Button disabled={busy} onClick={() => open(lobby)}>
                    Open playing scene
                  </Button>
                )}
              </div>
              <Button variant="outline" disabled={busy} onClick={restart}>
                Create another game
              </Button>
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
                    minutes · {brief.difficulty}
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
