import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import type { Lobby } from "./client";

type Summary = {
  id: string;
  version: number;
  revision: number;
  title: string;
  summary: string;
  status: string;
  published: boolean;
  archived: boolean;
};
type Finding = { code: string; message: string };
type GenerationJob = {
  id: string;
  scenario_id: string;
  owner_id: string;
  base_revision: number;
  base_version: number;
  instruction: string;
  section: "all" | "public" | "graph" | "party" | "gm_notes";
  status: "queued" | "running" | "needs_review" | "failed" | "cancelled";
  proposal_json: string | null;
  report: { status: string; findings: Finding[] } | null;
  error: string | null;
};
type View = {
  entry: Summary;
  revision: {
    draft: { edit: number; content_json: string };
    published: object | null;
  };
  current_report: {
    status: string;
    findings: Finding[];
  };
  generation_jobs: GenerationJob[];
};

export function ScenarioCatalog({
  token,
  generationAvailable,
  onCreate,
}: {
  token: string;
  generationAvailable?: boolean;
  onCreate: (lobby: Lobby) => void;
}) {
  const [providerAvailable, setProviderAvailable] = useState(
    generationAvailable ?? false,
  );
  const [entries, setEntries] = useState<Summary[]>([]);
  const [templates, setTemplates] = useState<
    { public: { title: string }; [key: string]: unknown }[]
  >([]);
  const [view, setView] = useState<View>();
  const [source, setSource] = useState("");
  const [instruction, setInstruction] = useState("");
  const [section, setSection] = useState<GenerationJob["section"]>("all");
  const [job, setJob] = useState<GenerationJob>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{
    path: string;
    body: object;
    game: boolean;
  }>();
  const request = async <T,>(path: string, body?: object): Promise<T> => {
    const response = await fetch(`/authoring/v1/scenarios${path}`, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      if (response.status < 500 && response.status !== 429)
        setPending(undefined);
      const value = (await response.json()) as { error?: string };
      throw new Error(value.error ?? "Scenario request failed");
    }
    return response.json() as Promise<T>;
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
  const reload = async () => setEntries(await request<Summary[]>(""));
  const open = async (id: string, revision?: number) => {
    const next = await request<View>(
      `/${id}${revision ? `?revision=${revision}` : ""}`,
    );
    setView(next);
    setSource(next.revision.draft.content_json);
    setJob(next.generation_jobs[next.generation_jobs.length - 1]);
    return next;
  };
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      fetch("/authoring/v1/scenarios", {
        headers: { Authorization: `Bearer ${token}` },
      }),
      fetch("/authoring/v1/scenarios/templates", {
        headers: { Authorization: `Bearer ${token}` },
      }),
      generationAvailable === undefined
        ? fetch("/setups/session", {
            headers: { Authorization: `Bearer ${token}` },
          })
        : Promise.resolve(undefined),
    ])
      .then(async ([saved, bundled, session]) => {
        if (!saved.ok || !bundled.ok || (session && !session.ok))
          throw new Error("Scenario catalog is unavailable on this server.");
        const values = (await saved.json()) as Summary[];
        const seeds = (await bundled.json()) as typeof templates;
        const sessionValue = session
          ? ((await session.json()) as { generation_available?: boolean })
          : undefined;
        if (
          !Array.isArray(values) ||
          !Array.isArray(seeds) ||
          seeds.some((seed) => !seed.public?.title)
        )
          throw new Error(
            "Scenario catalog returned an incompatible response.",
          );
        if (!cancelled) {
          setEntries(values);
          setTemplates(seeds);
          setProviderAvailable(
            generationAvailable ?? sessionValue?.generation_available ?? false,
          );
        }
      })
      .catch((e: unknown) => {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Unable to load scenarios");
      });
    return () => {
      cancelled = true;
    };
  }, [token, generationAvailable]);
  const send = async (value: { path: string; body: object; game: boolean }) => {
    setPending(value);
    if (value.game) {
      const lobby = await request<Lobby>(value.path, value.body);
      setPending(undefined);
      onCreate(lobby);
    } else {
      const saved = await request<Summary>(value.path, value.body);
      setPending(undefined);
      await reload();
      await open(saved.id, saved.revision);
    }
  };
  const write = (operation: string) =>
    run(async () => {
      const creating = operation === "create" || operation === "import";
      await send({
        path: creating ? "" : `/${view!.entry.id}`,
        game: false,
        body: {
          id: crypto.randomUUID(),
          operation,
          expected_version:
            creating || operation === "duplicate" ? 0 : view!.entry.version,
          ...(operation === "create" ||
          operation === "save" ||
          operation === "import"
            ? { content_json: source }
            : {}),
          ...(view && !creating ? { revision: view.revision.draft.edit } : {}),
        },
      });
    });
  const startGeneration = async () => {
    if (!instruction.trim()) throw new Error("Describe the change you want first.");
    let target = view;
    if (!target) {
      if (!source.trim())
        throw new Error("Choose a bundled template as a safe rules baseline first.");
      const saved = await request<Summary>("", {
        id: crypto.randomUUID(),
        operation: "create",
        expected_version: 0,
        content_json: source,
      });
      await reload();
      target = await open(saved.id, saved.revision);
    }
    const next = await request<GenerationJob>(`/${target.entry.id}/generate`, {
      id: crypto.randomUUID(),
      expected_version: target.entry.version,
      revision: target.revision.draft.edit,
      instruction: instruction.trim(),
      section,
    });
    setJob(next);
  };
  const refreshJob = async () => {
    if (!view || !job) return;
    setJob(
      await request<GenerationJob>(
        `/${view.entry.id}/generation-jobs/${job.id}`,
      ),
    );
  };
  const staleProposal =
    !!view &&
    !!job &&
    (job.base_version !== view.entry.version ||
      job.base_revision !== view.revision.draft.edit);
  return (
    <section aria-label="Scenario catalog">
      <h3>Scenario catalog</h3>
      <p>
        Save reusable scenarios before creating a game. Each game keeps its own
        copy of the selected published revision.
      </p>
      <label>
        Bundled scenario templates
        <select
          aria-label="Bundled scenario templates"
          defaultValue=""
          disabled={busy || !!pending}
          onChange={(e) => {
            const template = templates[Number(e.target.value)];
            if (template) {
              setView(undefined);
              setJob(undefined);
              setSource(JSON.stringify(template, null, 2));
            }
          }}
        >
          <option value="" disabled>
            Choose a template to author
          </option>
          {templates.map((t, i) => (
            <option key={i} value={i}>
              {t.public.title}
            </option>
          ))}
        </select>
      </label>
      <h4>My saved scenarios</h4>
      <Button disabled={busy || !!pending} onClick={() => void run(reload)}>
        Reload scenarios
      </Button>
      <Button
        disabled={busy || !!pending}
        onClick={() => {
          setView(undefined);
          setJob(undefined);
          setSource("");
        }}
      >
        New scenario
      </Button>
      <ul>
        {entries.map((entry) => (
          <li key={entry.id}>
            <Button
              disabled={busy || !!pending}
              onClick={() => void run(() => open(entry.id).then(() => undefined))}
            >
              {entry.title} · {entry.status} ·{" "}
              {entry.published ? "published" : "draft"}
              {entry.archived ? " · archived" : ""}
            </Button>
            <p>{entry.summary}</p>
          </li>
        ))}
      </ul>
      {view && (
        <label>
          Saved revision
          <select
            value={view.revision.draft.edit}
            disabled={busy || !!pending}
            onChange={(e) =>
              void run(() =>
                open(view.entry.id, Number(e.target.value)).then(() => undefined),
              )
            }
          >
            {Array.from({ length: view.entry.revision }, (_, i) => (
              <option key={i} value={i + 1}>
                {i + 1}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Scenario document JSON
        <textarea
          aria-label="Scenario document JSON"
          rows={12}
          maxLength={2000000}
          value={source}
          disabled={busy || !!pending || view?.entry.archived}
          onChange={(e) => setSource(e.target.value)}
        />
      </label>
      <p>
        Incomplete drafts can be saved. Import requires the v1 document format
        and matching server rules; it creates a new scenario identity.
      </p>
      {view && (
        <>
          <p role="status">
            Saved revision {view.revision.draft.edit} ·{" "}
            {view.current_report.status}
          </p>
          <ul>
            {view.current_report.findings.map((f, i) => (
              <li key={i}>
                {f.code}: {f.message}
              </li>
            ))}
          </ul>
        </>
      )}
      <article aria-label="AI scenario co-creation">
        <h4>Create or refine with AI</h4>
        <p>
          Describe the game or revision you want. The model can only propose a
          standard scenario document; it cannot publish, approve characters, or
          start play. You review the proposal before saving it.
        </p>
        {!providerAvailable && (
          <p role="status">
            AI authoring is unavailable on this server. Templates, saved
            scenarios, manual editing, import/export, and game creation remain
            available.
          </p>
        )}
        <label>
          What kind of game do you want?
          <textarea
            value={instruction}
            maxLength={4000}
            placeholder="A tense two-hour investigation with a rescue route, low lethality, and no supernatural horror."
            disabled={!providerAvailable || busy || !!pending}
            onChange={(e) => setInstruction(e.target.value)}
          />
        </label>
        <label>
          Refine section
          <select
            value={section}
            disabled={!providerAvailable || busy || !!pending}
            onChange={(e) => setSection(e.target.value as GenerationJob["section"])}
          >
            <option value="all">Whole scenario</option>
            <option value="public">Premise, genre, tone, boundaries and opening hook</option>
            <option value="graph">Scenes, objectives, alternatives and consequences</option>
            <option value="party">Party requirements and capabilities</option>
            <option value="gm_notes">Private GM notes</option>
          </select>
        </label>
        <div className="context-actions">
          <Button
            disabled={!providerAvailable || busy || !!pending || view?.entry.archived}
            onClick={() => void run(startGeneration)}
          >
            Generate proposal
          </Button>
          {job && (
            <Button disabled={busy} onClick={() => void run(refreshJob)}>
              Refresh proposal status
            </Button>
          )}
          {view && job && (job.status === "queued" || job.status === "running") && (
            <Button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setJob(
                    await request<GenerationJob>(
                      `/${view.entry.id}/generation-jobs/${job.id}/cancel`,
                      { id: crypto.randomUUID() },
                    ),
                  );
                })
              }
            >
              Cancel generation
            </Button>
          )}
        </div>
        {job && (
          <p role="status">
            Proposal {job.status} · section {job.section}
            {job.error ? ` · ${job.error}` : ""}
          </p>
        )}
        {job?.report && (
          <ul>
            {job.report.findings.map((f, i) => (
              <li key={i}>
                {f.code}: {f.message}
              </li>
            ))}
          </ul>
        )}
        {job?.status === "needs_review" && job.proposal_json && (
          <>
            <label>
              AI proposal JSON — review before accepting
              <textarea
                aria-label="AI proposal JSON"
                rows={12}
                readOnly
                value={job.proposal_json}
              />
            </label>
            {staleProposal && (
              <p role="alert">
                This proposal was generated from an older saved revision. Your
                newer edits are preserved; generate again from the current revision.
              </p>
            )}
            <Button
              disabled={busy || staleProposal || view?.entry.archived}
              onClick={() => setSource(job.proposal_json ?? source)}
            >
              Accept proposal into editor
            </Button>
            <p>
              Accepting only loads the proposal into the editor. Use Save scenario
              draft, Validate, Publish, and Create game explicitly below.
            </p>
          </>
        )}
      </article>
      <div className="context-actions">
        <Button
          disabled={busy || !!pending || view?.entry.archived}
          onClick={() => void write(view ? "save" : "create")}
        >
          Save scenario draft
        </Button>
        <Button
          disabled={busy || !!pending}
          onClick={() => void write("import")}
        >
          Import as new scenario
        </Button>
        {view && (
          <>
            <Button
              disabled={busy || !!pending || view.entry.archived}
              onClick={() => void write("validate")}
            >
              Validate saved revision
            </Button>
            <Button
              disabled={
                busy ||
                !!pending ||
                view.entry.archived ||
                !!view.revision.published
              }
              onClick={() => void write("publish")}
            >
              Publish saved revision
            </Button>
            <Button
              disabled={busy || !!pending}
              onClick={() => void write("duplicate")}
            >
              Duplicate saved revision
            </Button>
            <Button
              disabled={busy || !!pending || view.entry.archived}
              onClick={() => void write("archive")}
            >
              Archive scenario
            </Button>
            <Button
              disabled={busy || !!pending}
              onClick={() =>
                void run(async () => {
                  const response = await fetch(
                    `/authoring/v1/scenarios/${view.entry.id}/export?revision=${view.revision.draft.edit}`,
                    { headers: { Authorization: `Bearer ${token}` } },
                  );
                  if (!response.ok) throw new Error("Export failed");
                  const url = URL.createObjectURL(await response.blob());
                  const link = document.createElement("a");
                  link.href = url;
                  link.download = `scenario-${view.entry.id}-r${view.revision.draft.edit}.json`;
                  link.click();
                  URL.revokeObjectURL(url);
                })
              }
            >
              Export saved revision
            </Button>
            <Button
              disabled={
                busy ||
                !!pending ||
                !view.revision.published ||
                view.current_report.status !== "playable"
              }
              onClick={() =>
                void run(() =>
                  send({
                    path: `/${view.entry.id}/instantiate`,
                    game: true,
                    body: {
                      id: crypto.randomUUID(),
                      revision: view.revision.draft.edit,
                    },
                  }),
                )
              }
            >
              Create game from revision
            </Button>
          </>
        )}
        {pending && (
          <Button disabled={busy} onClick={() => void run(() => send(pending))}>
            Retry original scenario request
          </Button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}