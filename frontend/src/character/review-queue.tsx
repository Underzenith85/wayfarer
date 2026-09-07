import { useState } from "react";
import { Button } from "../components/ui/button";
import { LiveTransport } from "../play/live";
import type { components } from "./workshop.generated";

type Queue = components["schemas"]["WorkshopReviewQueue"];
type Submission = Queue["submissions"][number];
interface Review {
  revision: number;
  draft: {
    revision: number;
    content_json: string;
    spent: number;
    remaining: number;
    diagnostics: { message: string }[];
    findings: { message: string }[];
  };
}

/** GM review never opens a player transport or grants actor control. */
export function WorkshopReviewQueue({
  campaignId,
  principal,
  token,
}: {
  campaignId: string;
  principal: string;
  token: string;
}) {
  const [queue, setQueue] = useState<Queue | null>(null);
  const [selected, setSelected] = useState<Submission | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [reason, setReason] = useState("");
  const [target, setTarget] = useState("");
  const [points, setPoints] = useState(1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const transport = new LiveTransport(principal, campaignId, token);
  const request = (path: string, body?: object) =>
    transport.request(path, new AbortController().signal, body);
  const refresh = async () => {
    setQueue((await request("/workshop-reviews")) as Queue);
    setReview(null);
    setSelected(null);
  };
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label={`GM workshop ${campaignId}`}>
      <Button disabled={busy} onClick={() => void run(refresh)}>
        Review character submissions
      </Button>
      {error && <p role="alert">{error}</p>}
      {queue && (
        <>
          <h3>Submitted characters</h3>
          {queue.submissions.length === 0 && <p>No pending submissions</p>}
          {queue.submissions.map((entry) => (
            <Button
              key={entry.draft_id}
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const loaded = (await request(
                    `/workshop/${encodeURIComponent(entry.actor_id)}?draft_id=${encodeURIComponent(entry.draft_id)}`,
                  )) as Review;
                  setReview(loaded);
                  setSelected(entry);
                })
              }
            >
              Review {entry.name}
              {entry.approved ? " (approved)" : ""}
            </Button>
          ))}
          {review && selected && (
            <>
              <p>
                {review.draft.spent} points spent · {review.draft.remaining}{" "}
                remaining
              </p>
              <pre>
                {JSON.stringify(JSON.parse(review.draft.content_json), null, 2)}
              </pre>
              {[...review.draft.diagnostics, ...review.draft.findings].map(
                (item, i) => (
                  <p key={i}>{item.message}</p>
                ),
              )}
              <Button
                disabled={busy || !reason.trim()}
                onClick={() =>
                  void run(async () => {
                    await request("/drafts", {
                      id: crypto.randomUUID(),
                      draft_id: selected.draft_id,
                      actor_id: selected.actor_id,
                      expected_revision: review.revision,
                      expected_draft_revision: review.draft.revision,
                      operation: "approve",
                      kind: "character",
                      content_json: null,
                      reason,
                    } satisfies components["schemas"]["DraftCommand"]);
                    await refresh();
                  })
                }
              >
                Approve submitted draft
              </Button>
            </>
          )}
          <label>
            GM review or reward reason
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <label>
            Reward character
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Select character</option>
              {queue.actors.map((actor) => (
                <option key={actor.actor_id} value={actor.actor_id}>
                  {actor.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Earned point reward
            <input
              type="number"
              min={1}
              max={10000}
              value={points}
              onChange={(e) => setPoints(Number(e.target.value))}
            />
          </label>
          <Button
            disabled={busy || !reason.trim() || !target}
            onClick={() =>
              void run(async () => {
                await request("/workshop-grants", {
                  id: crypto.randomUUID(),
                  actor_id: principal,
                  target_actor_id: target,
                  expected_revision: queue.revision,
                  points,
                  reason,
                } satisfies components["schemas"]["GrantPoints"]);
                await refresh();
              })
            }
          >
            Grant earned points
          </Button>
        </>
      )}
    </section>
  );
}
