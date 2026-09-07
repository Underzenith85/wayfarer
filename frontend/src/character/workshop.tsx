import { useCallback, useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import { usePlay } from "../play/use-play";
import { LiveTransport } from "../play/live";
import { orderStats, statLabel } from "../presentation/labels";
import type { components } from "./workshop.generated";
type Options = components["schemas"]["WorkshopOptions"];
type ProfilePreview = components["schemas"]["ProfilePreviewResult"];
import {
  CharacterDraftEditor,
  type Proposal,
  type CharacterPreview,
} from "./draft-editor";
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
  submitted_revision: number | null;
  approved: boolean;
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
  const [selectedProfile, setSelectedProfile] = useState("");
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
  const activeProfile = `${data?.options.active_profile}@${data?.options.active_profile_version}`;
  const previewCharacter = useCallback(
    async (value: Proposal, signal: AbortSignal): Promise<CharacterPreview> => {
      if (!(transport instanceof LiveTransport) || !actor)
        throw new Error("Workshop unavailable");
      if (selectedProfile && selectedProfile !== activeProfile) {
        const split = selectedProfile.lastIndexOf("@");
        const result = (await transport.request(
          "/workshop-profile-preview",
          signal,
          {
            profile_id: selectedProfile.slice(0, split),
            version: Number(selectedProfile.slice(split + 1)),
            proposal: value,
          },
        )) as ProfilePreview;
        if (!signal.aborted) setProfilePreview(result);
        return result;
      }
      return (await transport.request(
        `/workshop/${encodeURIComponent(actor)}/preview`,
        signal,
        { proposal: value },
      )) as CharacterPreview;
    },
    [transport, actor, selectedProfile, activeProfile],
  );
  if (!(transport instanceof LiveTransport) || !actor || !data || !proposal)
    return error ? <p role="alert">{error}</p> : null;
  const foreignProfile =
    selectedProfile !== "" &&
    selectedProfile !==
      `${data.options.active_profile}@${data.options.active_profile_version}`;
  const change = (draft: Proposal["draft"]) => {
    setAdvancePreview(null);
    setProposal({ ...proposal, draft });
  };
  const chooseProfile = (value: string) => {
    setSelectedProfile(value);
    setProfilePreview(null);
    setAdvancePreview(null);
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
    operation: "save" | "submit" | "activate" | "approve",
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
      <Button
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setError("");
          void transport
            .request(
              `/workshop/${encodeURIComponent(actor)}`,
              new AbortController().signal,
            )
            .then((value) => {
              const loaded = value as Workshop;
              setData(loaded);
              setProposal(
                loaded.draft?.activated_revision == null && loaded.draft
                  ? (JSON.parse(loaded.draft.content_json) as Proposal)
                  : loaded.proposal,
              );
              setAdvancePreview(null);
            })
            .catch((e: unknown) =>
              setError(e instanceof Error ? e.message : "Refresh failed"),
            )
            .finally(() => setBusy(false));
        }}
      >
        Reload saved character
      </Button>
      <label htmlFor="workshop-profile">Rules profile</label>
      <select
        id="workshop-profile"
        disabled={busy}
        value={selectedProfile}
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
      <CharacterDraftEditor
        proposal={proposal}
        preview={previewCharacter}
        disabled={busy}
        onChange={(next) => {
          setAdvancePreview(null);
          setProposal(next);
        }}
      />
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
            {orderStats(data.draft.derived, (v) => v.target).map((v) => (
              <p key={v.target}>
                {statLabel({ id: v.target }).full}: {v.value}
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
              disabled={
                busy ||
                foreignProfile ||
                !reason.trim() ||
                JSON.stringify(proposal) !==
                  JSON.stringify(JSON.parse(data.draft.content_json))
              }
              onClick={() => void submit("approve")}
            >
              Approve draft
            </Button>
          )}
          <Button
            disabled={
              busy ||
              foreignProfile ||
              data.draft.activated_revision !== null ||
              data.draft.status === "illegal" ||
              data.draft.status === "blocked" ||
              JSON.stringify(proposal) !==
                JSON.stringify(JSON.parse(data.draft.content_json))
            }
            onClick={() => void submit("submit")}
          >
            Submit for GM review
          </Button>
          {data.draft.submitted_revision !== null && (
            <p>
              {data.draft.approved
                ? "GM approved this draft"
                : "Awaiting GM approval"}
            </p>
          )}
          <Button
            disabled={
              busy ||
              foreignProfile ||
              (data.draft.submitted_revision != null && !data.draft.approved) ||
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
