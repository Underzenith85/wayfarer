import { Link, useLocation } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { pagePath, parsePath, type Segment } from "./routes";
/** Builds paths that keep the campaign the current URL names. */
function useScopedPath() {
  const pathname = useLocation({ select: (location) => location.pathname });
  const campaignId = parsePath(pathname)?.campaignId ?? null;
  return (segment: Segment) => pagePath(campaignId, segment);
}
/** An in-app link that stays addressable to the open campaign. */
export function ScopedLink({
  segment,
  children,
}: {
  segment: Segment;
  children: ReactNode;
}) {
  const path = useScopedPath();
  return <Link to={path(segment)}>{children}</Link>;
}
