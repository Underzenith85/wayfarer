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
  adventures?: {
    adventure_id: string;
    title: string;
    outcome: string;
    at: number;
    evidence: { id: string; title: string; satisfied: boolean }[];
    discoveries: { id: string; predicate: string; value: string }[];
    casualties: string[];
    commitments: { id: string; description: string; status: string }[];
    rewards: { id: string; points: number; item_id: string | null }[];
    pools: { id: string; current: number; maximum: number }[];
    advancement: { id: string; kind: string; points: number; reason: string }[];
  }[];
  next_adventure?: { id: string; title: string; opening_action: string } | null;
}
export class SetupClient {
  private pending: { path: string; body: object } | null = null;
  constructor(private token: string) {}
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
      throw new Error(
        "Scenario generation is unavailable on this server. Choose an authored adventure.",
      );
    const result = (await response.json()) as T & { error?: string };
    if (!response.ok) throw new Error(result.error ?? "Setup request failed");
    return result;
  }
  /** Failed writes retain the exact command until explicitly retried/reconciled. */
  async write(path: string, body: object): Promise<Lobby> {
    if (this.pending)
      throw new Error("Retry or reload the pending setup request first.");
    this.pending = { path, body };
    return this.retry();
  }
  async retry(): Promise<Lobby> {
    if (!this.pending) throw new Error("No pending setup request");
    const { path, body } = this.pending;
    const result = await this.request<Lobby>(path, body);
    this.pending = null;
    return result;
  }
  get hasPending() {
    return this.pending !== null;
  }
  reconcile() {
    this.pending = null;
  }
}
