import { useEffect, useMemo, useState } from "react";
import { Button } from "../components/ui/button";
import { difficulties, difficultyLabel } from "../presentation/labels";
import type { Brief } from "./client";

type Finding = {
  code: string;
  severity: "error" | "warning";
  reference: string;
  message: string;
};
type GenerationJob = {
  id: string;
  version: number;
  status:
    | "queued"
    | "running"
    | "needs_review"
    | "succeeded"
    | "failed"
    | "cancelled";
  proposal_json: string | null;
  report: { status: string; findings: Finding[] } | null;
  error_code: string | null;
  error_message: string | null;
  request: { source_digest: string | null; source_json?: string | null };
};

const initialBrief: Brief = {
  premise: "",
  genre: "Fantasy",
  tone: "Adventurous",
  duration_minutes: 90,
  difficulty: "standard",
  restrictions: [],
};

async function digest(source: string) {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(source),
  );
  return Array.from(new Uint8Array(bytes), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
}

/**
 * What a provider failure leaves the author able to do. A missing provider is
 * not a defect in the request, so the advice names the next move rather than
 * asking for the same button again (#263).
 */
const failureAdvice: Record<string, string> = {
  provider_unavailable:
    "The AI provider did not answer. Check its login on the server, or write the scenario yourself below — nothing you have entered is lost.",
  provider_limited:
    "The AI provider refused further requests for now. Wait a little and retry, or write the scenario yourself below.",
};

export function GuidedScenarioAuthoring({
  token,
  principal,
  source,
  concept,
  onAccept,
}: {
  token: string;
  principal: string;
  source: string;
  /** The concept already captured in setup; the workshop never asks again (#262). */
  concept?: Brief | undefined;
  onAccept: (proposal: string) => void;
}) {
  const storageKey = `wayfarer-scenario-generation:${principal}`;
  const [open, setOpen] = useState(
    () => sessionStorage.getItem(storageKey) !== null,
  );
  /**
   * The workshop shows the concept setup already captured, so the same five
   * questions are never asked twice (#262). It holds a copy of its own only
   * once the author changes something here, and follows the concept step until
   * they do.
   */
  const [own, setOwn] = useState<Brief>();
  const brief = own ?? concept ?? initialBrief;
  const [instructions, setInstructions] = useState("");
  const [capabilities, setCapabilities] = useState("");
  const [section, setSection] = useState("all");
  const [job, setJob] = useState<GenerationJob>();
  const [error, setError] = useState("");
  const [authorMode, setAuthorMode] = useState(false);
  const [submittedSource, setSubmittedSource] = useState<string | null>(null);

  const edit = (value: Brief) => setOwn(value);
  const request = async (path: string, body?: object) => {
    const response = await fetch(
      `/authoring/v1/scenarios/generation-jobs${path}`,
      {
        method: body ? "POST" : "GET",
        headers: {
          Authorization: `Bearer ${token}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      },
    );
    const value = (await response.json()) as GenerationJob & { error?: string };
    if (!response.ok)
      throw new Error(value.error ?? "Generation request failed");
    return value;
  };

  const watch = async (id: string) => {
    const value = await request(`/${id}`);
    setSubmittedSource(
      (previous) => previous ?? value.request.source_json ?? "",
    );
    setJob(value);
    if (value.status === "queued" || value.status === "running") return false;
    sessionStorage.removeItem(storageKey);
    return true;
  };

  useEffect(() => {
    const saved = sessionStorage.getItem(storageKey);
    if (!saved) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const terminal = await watch(saved);
        if (!cancelled && !terminal) timer = setTimeout(() => void poll(), 500);
      } catch (reason) {
        if (!cancelled)
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to recover generation",
          );
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // The principal-specific key intentionally owns recovery for this mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  const publicProposal = useMemo(() => {
    if (!job?.proposal_json) return null;
    try {
      const value = JSON.parse(job.proposal_json) as {
        public?: {
          title?: string;
          summary?: string;
          opening_prompt?: string;
          setup?: Brief;
        };
        party?: { slots?: { actor_id: string; role: string }[] };
      };
      return value.public ? { public: value.public, party: value.party } : null;
    } catch {
      return null;
    }
  }, [job]);

  const generate = async () => {
    setError("");
    const sourceDigest = source ? await digest(source) : null;
    const id = crypto.randomUUID();
    setSubmittedSource(source);
    sessionStorage.setItem(storageKey, id);
    try {
      const value = await request("", {
        id,
        brief,
        instructions,
        party_capabilities: capabilities
          .split("\n")
          .map((value) => value.trim())
          .filter(Boolean),
        section: source ? section : "all",
        source_json: source || null,
        source_digest: sourceDigest,
        attempts: 2,
      });
      setJob(value);
      const poll = async () => {
        if (!(await watch(id))) setTimeout(() => void poll(), 500);
      };
      setTimeout(() => void poll(), 50);
    } catch (reason) {
      sessionStorage.removeItem(storageKey);
      setError(reason instanceof Error ? reason.message : "Generation failed");
    }
  };

  const running =
    !!job && (job.status === "queued" || job.status === "running");
  const failure = job?.status === "failed" ? job : undefined;
  /** A run to report: one that failed, or any the service left a message on. */
  const trouble = failure ?? (job?.error_message ? job : undefined);
  /** A run that ended without a proposal, however it ended, is retried in place. */
  const stalled =
    job && (job.status === "failed" || job.status === "cancelled")
      ? job
      : undefined;
  const changedSinceGeneration =
    !!job?.proposal_json &&
    submittedSource !== null &&
    source !== submittedSource;

  return (
    <section aria-label="Guided scenario creation" className="guided-authoring">
      <Button type="button" onClick={() => setOpen(!open)}>
        {open ? "Close AI workshop" : "Create with AI"}
      </Button>
      {open && (
        <>
          <p>
            Describe the experience you want. The model proposes an editable
            scenario; only you can accept, save, publish, and start it.
          </p>
          <label>
            Premise
            <textarea
              required
              maxLength={4000}
              value={brief.premise}
              onChange={(event) =>
                edit({ ...brief, premise: event.target.value })
              }
            />
          </label>
          <div className="guided-fields">
            <label>
              Genre
              <input
                value={brief.genre}
                onChange={(event) =>
                  edit({ ...brief, genre: event.target.value })
                }
              />
            </label>
            <label>
              Tone
              <input
                value={brief.tone}
                onChange={(event) =>
                  edit({ ...brief, tone: event.target.value })
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
                onChange={(event) =>
                  edit({
                    ...brief,
                    duration_minutes: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              Difficulty
              <select
                value={brief.difficulty}
                onChange={(event) =>
                  edit({
                    ...brief,
                    difficulty: event.target.value as Brief["difficulty"],
                  })
                }
              >
                {difficulties.map((value) => (
                  <option key={value} value={value}>
                    {difficultyLabel(value)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            Boundaries and rules restrictions (one per line)
            <textarea
              value={brief.restrictions.join("\n")}
              onChange={(event) =>
                edit({
                  ...brief,
                  restrictions: event.target.value.split("\n").filter(Boolean),
                })
              }
            />
          </label>
          <label>
            Party capabilities (optional, one per line)
            <textarea
              value={capabilities}
              onChange={(event) => setCapabilities(event.target.value)}
            />
          </label>
          <label>
            Refinement instructions
            <textarea
              placeholder="More investigation, a shorter adventure, and a rescue route…"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
            />
          </label>
          {source && (
            <label>
              Regenerate section
              <select
                value={section}
                onChange={(event) => setSection(event.target.value)}
              >
                <option value="all">Whole scenario</option>
                <option value="brief">Premise and tone</option>
                <option value="opening">Opening scene</option>
                <option value="world">World and routes</option>
                <option value="objectives">Objectives and endings</option>
                <option value="characters">Characters and encounters</option>
              </select>
            </label>
          )}
          <div className="context-actions">
            {/* Retry replaces Generate rather than joining it: one control runs
                generation, whatever the last attempt did (#263). */}
            {stalled ? (
              <Button
                type="button"
                onClick={() =>
                  void request(`/${stalled.id}/retry`, {
                    expected_version: stalled.version,
                  })
                    .then((value) => {
                      sessionStorage.setItem(storageKey, value.id);
                      setJob(value);
                      const poll = async () => {
                        if (!(await watch(value.id)))
                          setTimeout(() => void poll(), 500);
                      };
                      setTimeout(() => void poll(), 50);
                    })
                    .catch((reason: unknown) =>
                      setError(
                        reason instanceof Error
                          ? reason.message
                          : "Retry failed",
                      ),
                    )
                }
              >
                Retry generation
              </Button>
            ) : (
              <Button
                type="button"
                disabled={!brief.premise || running}
                onClick={() => void generate()}
              >
                {source
                  ? "Generate proposed changes"
                  : "Generate scenario proposal"}
              </Button>
            )}
            {running && (
              <Button
                type="button"
                onClick={() =>
                  void request(`/${job!.id}/cancel`, {})
                    .then(setJob)
                    .catch((reason: unknown) =>
                      setError(
                        reason instanceof Error
                          ? reason.message
                          : "Cancellation failed",
                      ),
                    )
                }
              >
                Cancel generation
              </Button>
            )}
          </div>
          {job && !trouble && (
            <p role="status">Generation: {job.status.replace("_", " ")}</p>
          )}
          {/* A failure is not help text: it is announced, it looks like a
              failure, and it says what the author can do next (#263). The
              provider's own code and the job identifier stay a disclosure,
              for the maintainer the author reports it to. */}
          {trouble && (
            <div className="generation-failure" role="alert">
              <strong>Generation failed</strong>
              <p>
                {trouble.error_message ??
                  "The provider did not return a usable scenario."}
              </p>
              <p>
                {failureAdvice[trouble.error_code ?? ""] ??
                  "Retry the generation, or write the scenario yourself below — nothing you have entered is lost."}
              </p>
              <details>
                <summary>Generation error details</summary>
                <p>Code: {trouble.error_code}</p>
                <p>Job ID: {trouble.id}</p>
              </details>
            </div>
          )}
          {publicProposal && (
            <article aria-label="Scenario proposal">
              <h4>{publicProposal.public.title}</h4>
              <p>{publicProposal.public.summary}</p>
              <p>
                Opening hook: {publicProposal.public.opening_prompt} · Party:{" "}
                {publicProposal.party?.slots
                  ?.map((slot) => slot.role)
                  .join(", ") || "Needs compatible characters"}
              </p>
              <ul>
                {job?.report?.findings.map((finding, index) => (
                  <li key={`${finding.code}-${index}`}>
                    {finding.severity === "error"
                      ? "Hard error"
                      : "Challenge warning"}
                    : {finding.message}
                  </li>
                ))}
              </ul>
              <label>
                <input
                  type="checkbox"
                  checked={authorMode}
                  onChange={(event) => setAuthorMode(event.target.checked)}
                />{" "}
                Authorized author/GM mode — show full scenario and spoilers
              </label>
              {authorMode && <pre>{job?.proposal_json}</pre>}
              {changedSinceGeneration && (
                <p role="alert">
                  The editor changed after generation started. Generate again so
                  newer edits are never overwritten.
                </p>
              )}
              <Button
                type="button"
                disabled={changedSinceGeneration}
                onClick={() => {
                  const proposal = job?.proposal_json;
                  if (proposal) {
                    setSubmittedSource(proposal);
                    onAccept(proposal);
                  }
                }}
              >
                Accept proposal into editor
              </Button>
              <p>
                Acceptance does not publish or start a game. Review and save
                below.
              </p>
            </article>
          )}
          {error && <p role="alert">{error}</p>}
        </>
      )}
    </section>
  );
}
