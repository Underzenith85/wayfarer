import type { components } from "../api/contracts.generated";
type Schemas = components["schemas"];
export type Campaign = Schemas["Campaign"];
export type Action = Schemas["Action"];
export type SubmitAction = Schemas["SubmitAction"];
export type ClarifyAction = Schemas["ClarifyAction"];
export type Intent = Schemas["Intent"];
export type Channel = "action" | "dialogue" | "ooc";
export interface Snapshot {
  campaign: Campaign;
  scene: Schemas["Scene"];
  characters: Schemas["Character"][];
  inventories: Schemas["Inventory"][];
  session: Schemas["Session"] | null;
  /** Presentation fixtures only until the proposed objective/party contracts freeze. */
  objectives: string[];
  party: { id: string; name: string; status: string }[];
}
export interface Narration {
  text: string;
  status: "provisional" | "complete" | "failed";
}
/** Facade for #49's generated transport; no HTTP endpoints or WebSocket envelopes invented here. */
export interface PlayTransport {
  readonly principalId: string;
  readonly sample: boolean;
  listCampaigns(signal: AbortSignal): Promise<Campaign[]>;
  readSnapshot(campaignId: string, signal: AbortSignal): Promise<Snapshot>;
  submitAction(
    campaignId: string,
    request: SubmitAction,
    signal: AbortSignal,
  ): Promise<Action>;
  clarifyAction(
    campaignId: string,
    actionId: string,
    request: ClarifyAction,
    signal: AbortSignal,
  ): Promise<Action>;
  listActions(campaignId: string, signal: AbortSignal): Promise<Action[]>;
  getAction(
    campaignId: string,
    actionId: string,
    signal: AbortSignal,
  ): Promise<Action>;
  /** Presentation stream, explicitly provisional; #48/#49 supply the future wire adapter. */
  narrate(
    campaignId: string,
    actionId: string,
    signal: AbortSignal,
  ): AsyncIterable<Narration>;
}
export class TransportError extends Error {
  constructor(
    public readonly code: Schemas["Error"]["code"] | "network",
    message: string,
  ) {
    super(message);
  }
}
const unavailable = () =>
  Promise.reject(
    new TransportError(
      "service_unavailable",
      "No campaign connection is configured.",
    ),
  );
export const disconnectedTransport: PlayTransport = {
  principalId: "disconnected",
  sample: false,
  listCampaigns: () => Promise.resolve([]),
  readSnapshot: unavailable,
  submitAction: unavailable,
  clarifyAction: unavailable,
  listActions: () => Promise.resolve([]),
  getAction: unavailable,
  async *narrate() {
    yield { text: "Narration is unavailable.", status: "failed" };
  },
};
export function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
