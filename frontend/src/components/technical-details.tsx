/**
 * Version digests and other engine bookkeeping. Kept behind a disclosure so the
 * loudest text in a player-facing panel is never a 64-character hash (#159).
 */
export function TechnicalDetails({
  entries,
}: {
  entries: { label: string; value: string }[];
}) {
  const shown = entries.filter((entry) => entry.value);
  if (!shown.length) return null;
  return (
    <details className="technical-details">
      <summary>Technical details</summary>
      {shown.map((entry) => (
        <p key={entry.label}>
          {entry.label} {entry.value}
        </p>
      ))}
    </details>
  );
}
