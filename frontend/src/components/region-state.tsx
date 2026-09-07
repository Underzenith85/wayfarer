/**
 * Empty and failed regions in player voice (#162).
 *
 * An empty collection and a region whose data never arrived used to read alike:
 * both printed one grey sentence, and that sentence explained itself in terms of
 * the transport ("not available from this connection"). Empty regions now say
 * what belongs there in the language of the game; a region that failed to load
 * is an alert with a retry, so a reader can tell "you own nothing" from
 * "we could not tell you what you own".
 */
import type { ReactNode } from "react";
import { Button } from "./ui/button";
import { TechnicalDetails } from "./technical-details";
/** A region with nothing in it yet. Names the region and what will fill it. */
export function EmptyRegion({ children }: { children: ReactNode }) {
  return <p className="empty-region">{children}</p>;
}
/**
 * A region that could not be loaded. Always an alert with a retry; the
 * underlying transport message stays behind the technical disclosure.
 */
export function UnavailableRegion({
  heading,
  headingLevel = 3,
  children,
  reason,
  busy = false,
  retryLabel = "Try again",
  onRetry,
}: {
  heading: string;
  headingLevel?: 2 | 3 | 4;
  children: ReactNode;
  reason?: string | null;
  busy?: boolean;
  retryLabel?: string;
  onRetry: () => void;
}) {
  const Heading = `h${headingLevel}` as const;
  return (
    <div className="unavailable-region" role="alert">
      <Heading>{heading}</Heading>
      <p>{children}</p>
      <Button variant="outline" disabled={busy} onClick={onRetry}>
        {retryLabel}
      </Button>
      <TechnicalDetails entries={[{ label: "Reason", value: reason ?? "" }]} />
    </div>
  );
}
