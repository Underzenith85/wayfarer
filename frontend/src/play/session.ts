/**
 * The signed-in session for one browser tab. Session storage keeps a reload
 * from costing a re-authentication, and never outlives the tab: closing it, or
 * ending or losing the session, drops the credential.
 */
export interface StoredSession {
  credential: string;
  principalId: string;
  campaignId: string | null;
}
const sessionKey = "wayfarer:session";
const routeKey = "wayfarer:requested-route";
export function readSession(): StoredSession | null {
  try {
    const saved = sessionStorage.getItem(sessionKey);
    if (!saved) return null;
    const value = JSON.parse(saved) as Partial<StoredSession>;
    if (!value.credential || !value.principalId) return null;
    return {
      credential: value.credential,
      principalId: value.principalId,
      campaignId: value.campaignId ?? null,
    };
  } catch {
    /* A malformed or unavailable record simply means signing in again. */
    return null;
  }
}
export function rememberSession(session: StoredSession) {
  try {
    sessionStorage.setItem(sessionKey, JSON.stringify(session));
  } catch {
    /* Storage can be disabled; the session then lasts until reload. */
  }
}
/** Keeps the open campaign when the same credential authenticates again. */
export function rememberPrincipal(credential: string, principalId: string) {
  const current = readSession();
  rememberSession({
    credential,
    principalId,
    campaignId:
      current && current.credential === credential ? current.campaignId : null,
  });
}
/** Records the open campaign so a reload restores it, not just the credential. */
export function rememberCampaign(campaignId: string | null) {
  const current = readSession();
  if (current) rememberSession({ ...current, campaignId });
}
export function forgetSession() {
  try {
    sessionStorage.removeItem(sessionKey);
    sessionStorage.removeItem(routeKey);
  } catch {
    /* Storage is optional. */
  }
}
/** Holds the view a visitor asked for while they sign in. */
export function rememberRequestedPath(path: string) {
  try {
    sessionStorage.setItem(routeKey, path);
  } catch {
    /* Storage is optional; sign-in then lands on the play view. */
  }
}
export function takeRequestedPath(): string | null {
  try {
    const path = sessionStorage.getItem(routeKey);
    sessionStorage.removeItem(routeKey);
    return path;
  } catch {
    return null;
  }
}
