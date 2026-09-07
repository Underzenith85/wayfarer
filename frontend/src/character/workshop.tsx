import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import { usePlay } from "../play/use-play";
import { LiveTransport } from "../play/live";
interface Proposal {
  draft: {
    name: string;
    backstory: string;
    purchases: { definition_id: string; amount: number }[];
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
  revision: number;
  proposal: Proposal;
  draft: Draft | null;
  catalog: { id: string; name: string }[];
}
export function CharacterWorkshop() {
  const { state, store } = usePlay();
  const transport = store.transport,
    actor = state.actorId;
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
  const change = (draft: Proposal["draft"]) =>
    setProposal({ ...proposal, draft });
  const submit = async (operation: "save" | "activate", generate = false) => {
    setBusy(true);
    setError("");
    try {
      const command = {
        id: crypto.randomUUID(),
        draft_id: data.draft?.id ?? `character:${actor}`,
        actor_id: actor,
        expected_revision: data.revision,
        expected_draft_revision: data.draft?.revision ?? 0,
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
                  j === i ? { ...v, definition_id: e.target.value } : v,
                ),
              })
            }
          >
            {data.catalog.map((d) => (
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
        </div>
      ))}
      <Button
        onClick={() =>
          change({
            ...proposal.draft,
            purchases: [
              ...proposal.draft.purchases,
              { definition_id: data.catalog[0]?.id ?? "", amount: 1 },
            ],
          })
        }
      >
        Add ability
      </Button>
      <Button disabled={busy} onClick={() => void submit("save")}>
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
        disabled={busy || !prompt.trim()}
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
          <Button
            disabled={
              busy ||
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
    </section>
  );
}
