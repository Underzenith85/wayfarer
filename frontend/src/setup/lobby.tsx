import { ScenarioCatalog } from "./catalog";
import { WorkshopReviewQueue } from "../character/review-queue";
import { LiveTransport } from "../play/live";
import type { Campaign } from "../play/transport";
import { useState } from "react";
import { Button } from "../components/ui/button";
import { NetworkPlayTransport } from "../api/play-transport";
import {
  campaignPhaseLabel,
  definitionLabel,
  humanize,
  lifecycleOperationLabel,
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
export function SetupLobby({
  onOpen,
  mode = "new",
}: {
  mode?: "new" | "continue" | "join";
  onOpen: (transport: PlayTransport) => void;
}) {
  const [token, setToken] = useState(""),
    [principal, setPrincipal] = useState("");
  const [games, setGames] = useState<Campaign[]>([]);
  const [legacyAvailable, setLegacyAvailable] = useState(false);
  const [generationAvailable, setGenerationAvailable] = useState(false);
  const [client, setClient] = useState<SetupClient>();
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
  const open = (value: Lobby) =>
    onOpen(
      new NetworkPlayTransport({
        origin: location.origin,
        credential: token,
        principalId: principal,
        initialCampaignId: value.id,
        engineControls: value.engine_controls ?? false,
      }),
    );
  const choose = (value: Lobby) => {
    setLobby(value);
    setBrief(value.brief);
    setGraph(value.graph);
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
  const host = lobby?.host_id === principal;
  const editable = !lobby || lobby.phase === "draft" || lobby.phase === "ready";
  return (
    <section className="scene-card setup-lobby" aria-label="New game and lobby">
      <h2>
        {mode === "new"
          ? "New game"
          : mode === "continue"
            ? "Continue game"
            : "Join game"}
      </h2>
      <p>Sign in with your own access token. No campaign ID is needed.</p>
      {!client ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const auth = await new SetupClient(token).request<{
                principal_id: string;
                generation_available: boolean;
                legacy_available: boolean;
              }>("/session");
              const next = new SetupClient(token, auth.principal_id);
              setPrincipal(auth.principal_id);
              setGenerationAvailable(auth.generation_available);
              setLegacyAvailable(auth.legacy_available);
              const games = await new NetworkPlayTransport({
                origin: location.origin,
                credential: token,
                principalId: auth.principal_id,
              }).listCampaigns(new AbortController().signal);
              setGames(games);
              const values = await next.request<Lobby[]>("");
              const available = await next.request<Graph[]>("/templates");
              const registered =
                await next.request<RulesProfile[]>("/profiles");
              setClient(next);
              setLobbies(values);
              setTemplates(available);
              setProfiles(registered);
            });
          }}
        >
          <label>
            Access token
            <input
              required
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </label>
          <Button disabled={busy}>Sign in</Button>
        </form>
      ) : (
        <>
          <div className="context-actions">
            <Button
              type="button"
              onClick={() => {
                setClient(undefined);
                setToken("");
                setPrincipal("");
                setGames([]);
                setTemplates([]);
                setLobbies([]);
                setLobby(undefined);
                setGraph(null);
                setBrief(blank);
              }}
            >
              Sign out of setup
            </Button>
            <Button
              type="button"
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
              Reload games / reconcile
            </Button>
          </div>
          {!generationAvailable && (
            <p>
              AI generation and free-text actions are unavailable. Authored
              adventures and scene action buttons work without an AI provider.
            </p>
          )}
          <p>
            {mode === "join"
              ? "Ask the host to invite your player name. Then reload to accept your invitation."
              : "Your saved games and unfinished drafts"}{" "}
            · Signed in as {principal}
          </p>
          <Button
            disabled={busy || client.hasPending}
            onClick={() => {
              setLobby(undefined);
              setGraph(null);
              setBrief(blank);
            }}
          >
            New draft
          </Button>
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
                        legacyAvailable
                          ? new LiveTransport(principal, game.id, token)
                          : new NetworkPlayTransport({
                              origin: location.origin,
                              credential: token,
                              principalId: principal,
                              initialCampaignId: game.id,
                            }),
                      )
                    }
                  >
                    Continue {game.name}
                  </Button>
                  {legacyAvailable && game.membership.role === "gm" && (
                    <WorkshopReviewQueue
                      key={`${principal}:${game.id}`}
                      campaignId={game.id}
                      principal={principal}
                      token={token}
                    />
                  )}
                </li>
              ))}
          </ul>
          {!lobby && (
            <ScenarioCatalog
              token={token}
              principal={principal}
              generationAvailable={generationAvailable}
              onCreate={(value) => {
                choose(value);
                setLobbies([...lobbies, value]);
              }}
            />
          )}
          {!lobby && <p>Create a game, or open an invitation above.</p>}
          {lobby && (
            <p role="status">
              {lobby.title} · {campaignPhaseLabel(lobby.phase)} · revision{" "}
              {lobby.revision}
            </p>
          )}
          {editable && (!lobby || host) && !lobby?.scenario_pinned && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
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
                        ? {
                            rules_profile: {
                              id: selected.id,
                              version: selected.version,
                            },
                          }
                        : {}),
                    });
                    choose(result);
                    setLobbies([...lobbies, result]);
                  } else
                    await command("edit", {
                      brief,
                      graph: graph ? { ...graph, brief } : null,
                    });
                });
              }}
            >
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
                  onChange={(e) => setBrief({ ...brief, tone: e.target.value })}
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
              <label>
                Adventure and starting party
                <select
                  value={graph?.id ?? ""}
                  onChange={(e) => {
                    const selected =
                      templates.find((t) => t.id === e.target.value) ?? null;
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
                  No authored adventures are installed. Save your premise, then
                  generate an adventure if a provider is configured.
                </p>
              )}
              {!lobby && profiles.length > 0 && (
                <label>
                  Rules profile
                  <select
                    value={profile}
                    onChange={(e) => setProfile(e.target.value)}
                  >
                    <option value="">Server default</option>
                    {profiles.map((p) => (
                      <option
                        key={`${p.id}@${p.version}`}
                        value={`${p.id}@${p.version}`}
                        disabled={!p.supported}
                      >
                        {p.title} (v{p.version})
                        {p.supported
                          ? ""
                          : ` · unavailable: ${p.unverified_capabilities.length} unverified capabilities`}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {graph?.actors
                .filter((a) => !graph.npc_actor_ids.includes(a.actor_id))
                .map((actor) => (
                  <fieldset key={actor.actor_id}>
                    <legend>Character {humanize(actor.actor_id)}</legend>
                    <div className="numeric-fields">
                      {actor.proposal.draft.purchases.map((purchase, index) => (
                        <label key={index}>
                          {definitionLabel(purchase.definition_id)}
                          <input
                            type="number"
                            min={0}
                            value={purchase.amount}
                            onChange={(e) =>
                              setGraph({
                                ...graph,
                                actors: graph.actors.map((a) =>
                                  a !== actor
                                    ? a
                                    : {
                                        ...a,
                                        proposal: {
                                          ...a.proposal,
                                          draft: {
                                            ...a.proposal.draft,
                                            purchases:
                                              a.proposal.draft.purchases.map(
                                                (p, i) =>
                                                  i === index
                                                    ? {
                                                        ...p,
                                                        amount: Number(
                                                          e.target.value,
                                                        ),
                                                      }
                                                    : p,
                                              ),
                                          },
                                        },
                                      },
                                ),
                              })
                            }
                          />
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ))}
              <p>
                Characters and equipment are checked against the server’s pinned
                rules before readiness. Saving edits clears assignments and
                readiness.
              </p>
              <Button disabled={busy || client.hasPending}>
                {lobby ? "Save setup draft" : "Create game draft"}
              </Button>
              {lobby && generationAvailable && (
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
          )}
          {lobby && (
            <>
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
                    {ending.casualties.join(", ") || "None visible"}
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
                        {p.id}: {p.current}/{p.maximum}
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
                    disabled={!generationAvailable || busy || client.hasPending}
                    onClick={() =>
                      void run(() => command("preview", {}, "/generate"))
                    }
                  >
                    Generate next-adventure preview
                  </Button>
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
              <details>
                <summary>
                  Campaign rules
                  {lobby.rules_profile
                    ? `: ${lobby.rules_profile.title} (v${lobby.rules_profile.version})`
                    : ""}
                </summary>
                <p>
                  Saved games keep their exact rules pins. Changing profiles is
                  an explicit host migration of a paused game.
                </p>
                <pre>{JSON.stringify(lobby.rules, null, 2)}</pre>
              </details>
              <ul className="seat-list" aria-label="Players">
                {lobby.seats.map((seat) => (
                  <li key={seat.principal_id} className="seat-row">
                    <span className="seat-player">{seat.principal_id}</span>
                    <span className="seat-character">
                      {seat.actor_ids.map(humanize).join(", ") ||
                        "No character assigned"}
                    </span>
                    <span className="seat-status">
                      {!seat.joined
                        ? "Invited"
                        : seat.ready
                          ? "Ready"
                          : "Not ready"}
                    </span>
                    {host && editable && seat.joined && lobby.graph && (
                      <label>
                        Assign character to {seat.principal_id}
                        <select
                          aria-label={`Assign character to ${seat.principal_id}`}
                          value={seat.actor_ids[0] ?? ""}
                          disabled={busy || client.hasPending}
                          onChange={(e) =>
                            void run(() =>
                              command("assign", {
                                principal_id: seat.principal_id,
                                actor_ids: e.target.value
                                  ? [e.target.value]
                                  : [],
                              }),
                            )
                          }
                        >
                          <option value="">Choose character</option>
                          {lobby.graph.actors
                            .filter(
                              (a) =>
                                !lobby.graph!.npc_actor_ids.includes(
                                  a.actor_id,
                                ),
                            )
                            .map((a) => (
                              <option key={a.actor_id} value={a.actor_id}>
                                {humanize(a.actor_id)}
                              </option>
                            ))}
                        </select>
                      </label>
                    )}
                  </li>
                ))}
              </ul>
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
              {editable && host && (
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
                  <Button
                    disabled={busy}
                    onClick={() =>
                      onOpen(
                        new NetworkPlayTransport({
                          origin: location.origin,
                          credential: token,
                          principalId: principal,
                          initialCampaignId: lobby.id,
                          engineControls: lobby.engine_controls ?? false,
                        }),
                      )
                    }
                  >
                    Open playing scene
                  </Button>
                )}
              </div>
              <Button
                variant="outline"
                disabled={busy || client.hasPending}
                onClick={() => {
                  setLobby(undefined);
                  setGraph(null);
                  setBrief(blank);
                }}
              >
                Create another game
              </Button>
            </>
          )}
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
