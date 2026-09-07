/**
 * Route shape for the campaign companion. Every view is addressable twice: as a
 * bare page for a session with no campaign open, and as `/c/:campaignId/<page>`
 * so a player can bookmark their own sheet and a GM can link a teammate to one
 * campaign's view. The server and the offline shell mirror this list.
 */
export const destinations = [
  { segment: "", name: "Play" },
  { segment: "character", name: "Character" },
  { segment: "inventory", name: "Inventory" },
  { segment: "journal", name: "Journal" },
  { segment: "campaign", name: "Campaign" },
] as const;
export type Segment = (typeof destinations)[number]["segment"];
export interface Route {
  campaignId: string | null;
  segment: Segment;
}
export function pagePath(campaignId: string | null, segment: Segment): string {
  const page = segment ? `/${segment}` : "";
  return campaignId
    ? `/c/${encodeURIComponent(campaignId)}${page}`
    : page || "/";
}
/** Null for any path outside the companion, so the shell can report a miss. */
export function parsePath(pathname: string): Route | null {
  const parts = pathname.split("/").filter(Boolean);
  let campaignId: string | null = null;
  if (parts[0] === "c") {
    if (!parts[1]) return null;
    try {
      campaignId = decodeURIComponent(parts[1]);
    } catch {
      // A malformed escape names no campaign we could open.
      return null;
    }
    parts.splice(0, 2);
  }
  if (parts.length > 1) return null;
  const segment = parts[0] ?? "";
  const known = destinations.find((item) => item.segment === segment);
  return known ? { campaignId, segment: known.segment } : null;
}
