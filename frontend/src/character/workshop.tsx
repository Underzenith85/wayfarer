import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import { usePlay } from "../play/use-play";
import { LiveTransport } from "../play/live";
import type { components } from "./workshop.generated";
type Options = components["schemas"]["WorkshopOptions"];
type ProfilePreview = components["schemas"]["ProfilePreviewResult"];
type TraitOptions = components["schemas"]["TraitOptions"];
interface Proposal {
  draft: {
    name: string;
    backstory: string;
    purchases: {
      definition_id: string;
      amount: number;
      trait?: TraitOptions | null;
    }[];
  };
  custom: unknown[];
}
interface Draft {
  id: string;
  revision: number;
  content_json: string;
  status: string;
  spent: number;
  remaining: number;
  diagnostics: { code: string; message: string }[];
  findings: { message: string }[];
  derived: { target: string; value: string }[];
  repair: Proposal["draft"] | null;
  activated_revision: number | null;
}
interface Workshop {
  options: Options;
  revision: number;
  proposal: Proposal;
  draft: Draft | null;
  catalog: { id: string; name: string }[];
}
export function CharacterWorkshop() {
  const { state, store } = usePlay();
  const transport = store.transport,
    actor = state.actorId;
  const [profilePreview, setProfilePreview] = useState<ProfilePreview | null>(
    null,
  );
  const [advancePreview, setAdvancePreview] = useState<{
    points_delta: number;
    points_available: number;
  } | null>(null);
  const [reason, setReason] = useState("");
  const [data, setData] = useState<Workshop | null>(null),
    [proposal, setProposal] = useState<Proposal | null>(null),
    [prompt, setPrompt] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!(transport instanceof LiveTransport) || !actor) return;
    const controller = new AbortController();
    void transport
      .request(`/workshop/${encodeURIComponent(actor)}`, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        const loaded = value as Workshop;
        setData(loaded);
        setProposal(
          loaded.draft
            ? (JSON.parse(loaded.draft.content_json) as Proposal)
            : loaded.proposal,
        );
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "Workshop unavailable");
      });
    return () => controller.abort();
  }, [transport, actor]);
  if (!(transport instanceof LiveTransport) || !actor || !data || !proposal)
    return null;
  const foreignProfile =
    profilePreview !== null &&
    profilePreview.profile.id !== data.options.active_profile;
  const catalog = profilePreview?.catalog ?? data.options.catalog;
  const change = (draft: Proposal["draft"]) => {
    setAdvancePreview(null);
    setProposal({ ...proposal, draft });
  };
  const chooseProfile = async (value: string) => {
    setError("");
    if (!value) {
      setProfilePreview(null);
      return;
    }
    const selected = data.options.profiles.find(
      (p) => `${p.id}@${p.version}` === value,
    );
    if (!selected) return;
    setBusy(true);
    try {
      setProfilePreview(
        (await transport.request(
          "/workshop-profile-preview",
          new AbortController().signal,
          { profile_id: selected.id, version: selected.version, proposal },
        )) as ProfilePreview,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Profile preview unavailable");
    } finally {
      setBusy(false);
    }
  };
  const advance = async (operation: "preview" | "apply") => {
    if (!data.options.build_revision) return;
    setBusy(true);
    setError("");
    try {
      const result = await transport.request(
        `/workshop-advancement/${operation}`,
        new AbortController().signal,
        {
          id: crypto.randomUUID(),
          actor_id: actor,
          expected_revision: data.revision,
          expected_build_revision: data.options.build_revision,
          draft: proposal.draft,
          reason,
        },
      );
      if (operation === "preview")
        setAdvancePreview(
          result as { points_delta: number; points_available: number },
        );
      else {
        const loaded = (await transport.request(
          `/workshop/${encodeURIComponent(actor)}`,
          new AbortController().signal,
        )) as Workshop;
        setData(loaded);
        setProposal(loaded.proposal);
        setAdvancePreview(null);
        await store.refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Advancement failed");
    } finally {
      setBusy(false);
    }
  };
  const submit = async (
    operation: "save" | "activate" | "approve",
    generate = false,
  ) => {
    setBusy(true);
    setError("");
    try {
      const command = {
        id: crypto.randomUUID(),
        draft_id:
          data.draft?.activated_revision != null
            ? `character:${actor}:${crypto.randomUUID()}`
            : (data.draft?.id ?? `character:${actor}`),
        actor_id: actor,
        expected_revision: data.revision,
        expected_draft_revision:
          data.draft?.activated_revision != null
            ? 0
            : (data.draft?.revision ?? 0),
        reason,
        operation,
        content_json: operation === "save" ? JSON.stringify(proposal) : null,
      };
      await transport.request(
        generate ? "/generate-draft" : "/drafts",
        new AbortController().signal,
        generate ? { command, prompt } : command,
      );
      const loaded = (await transport.request(
        `/workshop/${encodeURIComponent(actor)}`,
        new AbortController().signal,
      )) as Workshop;
      setData(loaded);
      setProposal(
        loaded.draft
          ? (JSON.parse(loaded.draft.content_json) as Proposal)
          : loaded.proposal,
      );
      await store.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Draft could not be saved");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="scene-card character-workshop">
      <h2>Character workshop</h2>
      <p>Draft changes are separate from your active character.</p>
      <label htmlFor="workshop-profile">Rules profile</label>
      <select
        id="workshop-profile"
        disabled={busy}
        defaultValue=""
        onChange={(e) => void chooseProfile(e.target.value)}
      >
        <option value="">Current campaign profile</option>
        {data.options.profiles.map((p) => (
          <option key={`${p.id}@${p.version}`} value={`${p.id}@${p.version}`}>
            {p.title} (v{p.version})
          </option>
        ))}
      </select>
      {profilePreview && (
        <section aria-label="Profile preview">
          <p>
            {profilePreview.spent} points spent · {profilePreview.remaining}{" "}
            remaining
          </p>
          {profilePreview.diagnostics.map((message, i) => (
            <p key={i}>{message}</p>
          ))}
          {profilePreview.derived.map(([target, value]) => (
            <p key={target}>
              {target}: {value}
            </p>
          ))}
          {foreignProfile && (
            <p>
              Preview only. Changing the campaign profile requires an explicit
              migration.
            </p>
          )}
          {!profilePreview.profile.supported && (
            <details>
              <summary>Unavailable mechanics</summary>
              {profilePreview.profile.blockers.map((id) => (
                <p key={id}>{id}</p>
              ))}
            </details>
          )}
        </section>
      )}
      {error && <p role="alert">{error}</p>}
      <label htmlFor="character-name">Name</label>
      <input
        id="character-name"
        value={proposal.draft.name}
        onChange={(e) => change({ ...proposal.draft, name: e.target.value })}
      />
      <label htmlFor="character-backstory">Concept and backstory</label>
      <textarea
        id="character-backstory"
        value={proposal.draft.backstory}
        onChange={(e) =>
          change({ ...proposal.draft, backstory: e.target.value })
        }
      />
      {proposal.draft.purchases.map((p, i) => (
        <div key={i} className="context-actions">
          <label htmlFor={`purchase-${i}`}>Ability {i + 1}</label>
          <select
            id={`purchase-${i}`}
            value={p.definition_id}
            onChange={(e) =>
              change({
                ...proposal.draft,
                purchases: proposal.draft.purchases.map((v, j) =>
                  j === i ? { definition_id: e.target.value, amount: 1 } : v,
                ),
              })
            }
          >
            {catalog.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <label htmlFor={`amount-${i}`}>Amount</label>
          <input
            id={`amount-${i}`}
            type="number"
            min={1}
            max={10000}
            value={p.amount}
            onChange={(e) =>
              change({
                ...proposal.draft,
                purchases: proposal.draft.purchases.map((v, j) =>
                  j === i ? { ...v, amount: Number(e.target.value) } : v,
                ),
              })
            }
          />
          <Button
            onClick={() =>
              change({
                ...proposal.draft,
                purchases: proposal.draft.purchases.filter((_, j) => j !== i),
              })
            }
          >
            Remove ability {i + 1}
          </Button>
          {(() => {
            const definition = catalog.find((d) => d.id === p.definition_id);
            const updateTrait = (trait: TraitOptions) =>
              change({
                ...proposal.draft,
                purchases: proposal.draft.purchases.map((value, j) =>
                  j === i ? { ...value, trait } : value,
                ),
              });
            const trait = p.trait ?? {
              parameters: [],
              modifiers: [],
              self_control: null,
            };
            return (
              <>
                {definition?.skill && (
                  <p>
                    {definition.skill.attribute} / {definition.skill.difficulty}
                    {definition.skill.specialty &&
                      ` · ${definition.skill.specialty.name}`}
                    {definition.skill.technique &&
                      ` · Technique: ${definition.skill.technique.parent}, cap +${definition.skill.technique.maximum_modifier}`}
                    {definition.skill.defaults
                      .map((d) => ` · Default: ${d.target} ${d.modifier}`)
                      .join("")}
                  </p>
                )}
                {definition?.trait?.self_control && (
                  <label>
                    Self-control
                    <select
                      aria-label={`Self-control ${i + 1}`}
                      value={trait.self_control ?? ""}
                      onChange={(e) =>
                        updateTrait({
                          ...trait,
                          self_control: Number(e.target.value) as
                            6 | 9 | 12 | 15,
                        })
                      }
                    >
                      <option value="" disabled>
                        Select rating
                      </option>
                      {[6, 9, 12, 15].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                )}
                {definition?.trait?.parameters.map((parameter) => (
                  <label key={parameter.name}>
                    {parameter.name}
                    <select
                      aria-label={`${parameter.name} ${i + 1}`}
                      value={
                        JSON.stringify(
                          trait.parameters.find(
                            (v) => v[0] === parameter.name,
                          )?.[1],
                        ) ?? ""
                      }
                      onChange={(e) =>
                        updateTrait({
                          ...trait,
                          parameters: [
                            ...trait.parameters.filter(
                              (v) => v[0] !== parameter.name,
                            ),
                            [
                              parameter.name,
                              JSON.parse(e.target.value) as
                                string | number | boolean,
                            ],
                          ],
                        })
                      }
                    >
                      <option value="" disabled>
                        Select value
                      </option>
                      {parameter.choices.map((value) => (
                        <option
                          key={JSON.stringify(value)}
                          value={JSON.stringify(value)}
                        >
                          {String(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
                {definition?.trait?.modifiers.map((modifier) => (
                  <label key={modifier.id}>
                    <input
                      type="checkbox"
                      checked={trait.modifiers.includes(modifier.id)}
                      onChange={(e) =>
                        updateTrait({
                          ...trait,
                          modifiers: e.target.checked
                            ? [...trait.modifiers, modifier.id]
                            : trait.modifiers.filter(
                                (id) => id !== modifier.id,
                              ),
                        })
                      }
                    />
                    {modifier.id} ({modifier.percent}%)
                  </label>
                ))}
              </>
            );
          })()}
        </div>
      ))}
      <Button
        onClick={() =>
          change({
            ...proposal.draft,
            purchases: [
              ...proposal.draft.purchases,
              { definition_id: catalog[0]?.id ?? "", amount: 1 },
            ],
          })
        }
      >
        Add ability
      </Button>
      <Button
        disabled={busy || foreignProfile}
        onClick={() => void submit("save")}
      >
        Save and validate
      </Button>
      <label htmlFor="workshop-prompt">Ask the character assistant</label>
      <textarea
        id="workshop-prompt"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        maxLength={4000}
      />
      <Button
        disabled={busy || foreignProfile || !prompt.trim()}
        onClick={() => void submit("save", true)}
      >
        Generate draft
      </Button>
      {data.draft && (
        <>
          <p role="status">
            {data.draft.status} · {data.draft.spent} points spent ·{" "}
            {data.draft.remaining} remaining
          </p>
          {data.draft.diagnostics.map((d, i) => (
            <p key={i}>{d.message}</p>
          ))}
          {data.draft.findings.map((d, i) => (
            <p key={i}>{d.message}</p>
          ))}
          <details>
            <summary>Derived statistics</summary>
            {data.draft.derived.map((v) => (
              <p key={v.target}>
                {v.target}: {v.value}
              </p>
            ))}
          </details>
          {data.draft.repair && (
            <Button onClick={() => change(data.draft!.repair!)}>
              Apply suggested repair
            </Button>
          )}
          {data.options.can_approve && (
            <Button
              disabled={busy || foreignProfile || !reason.trim()}
              onClick={() => void submit("approve")}
            >
              Approve draft
            </Button>
          )}
          <Button
            disabled={
              busy ||
              foreignProfile ||
              JSON.stringify(proposal) !==
                JSON.stringify(JSON.parse(data.draft.content_json)) ||
              data.draft.status === "illegal" ||
              data.draft.status === "blocked" ||
              data.draft.activated_revision !== null
            }
            onClick={() => void submit("activate")}
          >
            Activate approved draft
          </Button>
        </>
      )}
      <label htmlFor="workshop-reason">Approval or advancement reason</label>
      <input
        id="workshop-reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <p>Earned points available: {data.options.points_available}</p>
      <Button
        disabled={busy || foreignProfile || !reason.trim()}
        onClick={() => void advance("preview")}
      >
        Preview advancement
      </Button>
      {advancePreview && (
        <>
          <p>
            Cost: {advancePreview.points_delta} of{" "}
            {advancePreview.points_available} earned points
          </p>
          <Button
            disabled={busy || foreignProfile}
            onClick={() => void advance("apply")}
          >
            Apply advancement
          </Button>
        </>
      )}
    </section>
  );
}
