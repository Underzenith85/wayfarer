import { useQuery } from "@tanstack/react-query";
import type { CampaignAdapter } from "../api/adapter";
import { Button } from "./ui/button";
export function CampaignState({ adapter }: { adapter: CampaignAdapter }) {
  const query = useQuery({
    queryKey: ["current-campaign"],
    queryFn: ({ signal }) => adapter.getCurrentCampaign(signal),
    retry: false,
  });
  if (query.fetchStatus === "paused")
    return (
      <div role="status">
        <h2>Waiting for a connection</h2>
        <p>Your campaign will load when you’re back online.</p>
      </div>
    );
  if (query.isPending)
    return (
      <div role="status" aria-busy="true">
        <h2>Loading your campaign…</h2>
        <div className="skeleton" />
      </div>
    );
  if (query.isError)
    return (
      <div role="alert">
        <h2>Campaign unavailable</h2>
        <p>We couldn’t load your campaign. Try again.</p>
        <Button onClick={() => void query.refetch()}>Try again</Button>
      </div>
    );
  if (!query.data)
    return (
      <div className="empty-state">
        <span className="eyebrow">Your next chapter</span>
        <h2>No campaign selected</h2>
        <p>
          Your scene and the game master’s narration will appear here when you
          connect to a campaign.
        </p>
      </div>
    );
  return (
    <article className="narration">
      <span className="eyebrow">{query.data.name}</span>
      <h2>{query.data.scene}</h2>
      <p>{query.data.narration}</p>
    </article>
  );
}
