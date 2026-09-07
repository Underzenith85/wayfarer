import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import type { Lobby } from "./client";
import { GuidedScenarioAuthoring } from "./guided";

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
type View = {
  entry: Summary;
  revision: {
    draft: { edit: number; content_json: string };
    published: object | null;
  };
  current_report: {
    status: string;
    findings: { code: string; message: string }[];
  };
};

export function ScenarioCatalog({
  token,
  principal,
  generationAvailable,
  onCreate,
}: {
  token: string;
  principal: string;
  generationAvailable: boolean;
  onCreate: (lobby: Lobby) => void;
}) {
  const [entries, setEntries] = useState<Summary[]>([]);
  const [templates, setTemplates] = useState<
    { public: { title: string }; [key: string]: unknown }[]
  >([]);
  const [view, setView] = useState<View>();
  const [source, setSource] = useState("");
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
    ])
      .then(async ([saved, bundled]) => {
        if (!saved.ok || !bundled.ok)
          throw new Error("Scenario catalog is unavailable on this server.");
        const values = (await saved.json()) as Summary[];
        const seeds = (await bundled.json()) as typeof templates;
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
        }
      })
      .catch((e: unknown) => {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Unable to load scenarios");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);
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
  return (
    <section aria-label="Scenario catalog">
      <h3>Scenario catalog</h3>
      <p>
        Save reusable scenarios before creating a game. Each game keeps its own
        copy of the selected published revision.
      </p>
      {generationAvailable ? (
        <GuidedScenarioAuthoring
          token={token}
          principal={principal}
          source={source}
          onAccept={setSource}
        />
      ) : (
        <p>
          AI creation is unavailable. Templates, saved scenarios, manual
          editing, import, and export remain available.
        </p>
      )}
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
              onClick={() => void run(() => open(entry.id))}
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
              void run(() => open(view.entry.id, Number(e.target.value)))
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
