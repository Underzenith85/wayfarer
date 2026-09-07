export interface Brief {
  premise: string;
  genre: string;
  tone: string;
  duration_minutes: number;
  difficulty: "gentle" | "standard" | "hard";
  restrictions: string[];
}
export interface Graph {
  id: string;
  title: string;
  brief: Brief;
  npc_actor_ids: string[];
  actors: {
    actor_id: string;
    proposal: {
      draft: { purchases: { definition_id: string; amount: number }[] };
    };
  }[];
  [key: string]: unknown;
}
export interface Lobby {
  id: string;
  revision: number;
  title: string;
  host_id: string;
  phase: "draft" | "ready" | "active" | "paused" | "completed" | "archived";
  brief: Brief;
  graph: Graph | null;
  seats: {
    principal_id: string;
    joined: boolean;
    ready: boolean;
    actor_ids: string[];
  }[];
  rules: unknown;
}
class SetupRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
export class SetupClient {
  private pending: { path: string; body: object } | null = null;
  private storageKey?: string;
  constructor(
    private token: string,
    principal?: string,
  ) {
    if (principal) {
      this.storageKey = `wayfarer-setup-pending:${principal}`;
      const saved = sessionStorage.getItem(this.storageKey);
      if (saved)
        this.pending = JSON.parse(saved) as { path: string; body: object };
    }
  }
  private remember() {
    if (!this.storageKey) return;
    if (this.pending)
      sessionStorage.setItem(this.storageKey, JSON.stringify(this.pending));
    else sessionStorage.removeItem(this.storageKey);
  }
  async request<T>(path: string, body?: object): Promise<T> {
    const response = await fetch(`/setups${path}`, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (response.status === 404 && path.endsWith("/generate"))
      throw new SetupRequestError(
        "Scenario generation is unavailable on this server. Choose an authored adventure.",
        404,
      );
    const result = (await response.json()) as T & { error?: string };
    if (!response.ok)
      throw new SetupRequestError(
        result.error ?? "Setup request failed",
        response.status,
      );
    return result;
  }
  /** Failed writes retain the exact command until explicitly retried/reconciled. */
  async write(path: string, body: object): Promise<Lobby> {
    if (this.pending)
      throw new Error("Retry or reload the pending setup request first.");
    this.pending = { path, body: structuredClone(body) };
    this.remember();
    return this.retry();
  }
  async retry(): Promise<Lobby> {
    if (!this.pending) throw new Error("No pending setup request");
    const { path, body } = this.pending;
    try {
      const result = await this.request<Lobby>(path, body);
      this.pending = null;
      this.remember();
      return result;
    } catch (error) {
      if (
        error instanceof SetupRequestError &&
        error.status < 500 &&
        error.status !== 429
      ) {
        this.pending = null;
        this.remember();
      }
      throw error;
    }
  }
  get hasPending() {
    return this.pending !== null;
  }
}
