import type {
  InventoryCommand,
  InventoryIntent,
  InventoryOperation,
} from "../character/presentation";
import {
  TransportError,
  wait,
  type Action,
  type Campaign,
  type Channel,
  type ClarifyAction,
  type Intent,
  type Narration,
  type PlayTransport,
  type Snapshot,
  type SubmitAction,
} from "./transport";
export interface Entry {
  id: string;
  channel: Channel;
  text: string;
  action: Action | null;
  narration: Narration | null;
}
type Command =
  | {
      kind: "submit";
      request: SubmitAction;
      entryId: string;
      clearDraft: boolean;
    }
  | { kind: "inventory"; request: InventoryCommand; entryId: string }
  | {
      kind: "clarify";
      actionId: string;
      request: ClarifyAction;
      entryId: string;
    };
export interface PlayState {
  drafts: Record<Channel, string>;
  campaigns: Campaign[];
  snapshot: Snapshot | null;
  selectedId: string | null;
  entries: Entry[];
  loading: boolean;
  busy: boolean;
  error: string | null;
  expired: boolean;
  needsRefresh: boolean;
  retry: Command | null;
  actorId: string | null;
}
const initial: PlayState = {
  drafts: { action: "", dialogue: "", ooc: "" },
  campaigns: [],
  snapshot: null,
  selectedId: null,
  entries: [],
  loading: false,
  busy: false,
  error: null,
  expired: false,
  needsRefresh: false,
  retry: null,
  actorId: null,
};
/** A generation fences all async callbacks. Switching scope aborts reads/polls/streams and clears caches. */
export class PlayStore {
  private state: PlayState = { ...initial };
  private listeners = new Set<() => void>();
  private controller = new AbortController();
  private generation = 0;
  constructor(
    readonly transport: PlayTransport,
    private clearCache: () => void = () => {},
    private pollMs = 1000,
  ) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private patch(value: Partial<PlayState>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  private entry(id: string, value: Partial<Entry>) {
    this.patch({
      entries: this.state.entries.map((e) =>
        e.id === id ? { ...e, ...value } : e,
      ),
    });
  }
  private fence() {
    this.controller.abort();
    this.controller = new AbortController();
    this.generation++;
    this.clearCache();
  }
  private active(generation: number) {
    return generation === this.generation && !this.controller.signal.aborted;
  }
  private storageKey() {
    const s = this.state.snapshot;
    return s && this.state.actorId
      ? `wayfarer:draft:${this.transport.principalId}:${s.campaign.id}:${s.scene.id}:${this.state.actorId}`
      : null;
  }
  readDraft(channel: Channel) {
    try {
      const key = this.storageKey();
      return key ? (localStorage.getItem(`${key}:${channel}`) ?? "") : "";
    } catch {
      return "";
    }
  }
  saveDraft(channel: Channel, text: string) {
    this.patch({ drafts: { ...this.state.drafts, [channel]: text } });
    try {
      const key = this.storageKey();
      if (key) {
        if (text) localStorage.setItem(`${key}:${channel}`, text);
        else localStorage.removeItem(`${key}:${channel}`);
      }
    } catch {
      /* Drafts still work in memory when storage is disabled. */
    }
  }
  private hydrateDrafts() {
    this.patch({
      drafts: {
        action: this.readDraft("action"),
        dialogue: this.readDraft("dialogue"),
        ooc: this.readDraft("ooc"),
      },
    });
  }
  private clearPrivateStorage() {
    try {
      const prefix = `wayfarer:draft:${this.transport.principalId}:`;
      for (const key of Object.keys(localStorage))
        if (key.startsWith(prefix)) localStorage.removeItem(key);
      sessionStorage.removeItem(
        `wayfarer:resume:${this.transport.principalId}`,
      );
    } catch {
      /* Storage is optional. */
    }
  }
  expire() {
    this.fence();
    this.clearPrivateStorage();
    this.patch({
      ...initial,
      expired: true,
      error: "Your session expired or access changed. Reconnect to continue.",
    });
  }
  dispose() {
    this.fence();
    this.listeners.clear();
  }
  async loadCampaigns() {
    const g = this.generation;
    this.patch({ loading: true, error: null });
    try {
      const campaigns = await this.transport.listCampaigns(
        this.controller.signal,
      );
      if (this.active(g)) this.patch({ campaigns, loading: false });
    } catch (error) {
      this.fail(error, g);
    }
  }
  async select(campaignId: string) {
    this.fence();
    const g = this.generation;
    this.patch({
      snapshot: null,
      needsRefresh: false,
      selectedId: campaignId,
      entries: [],
      loading: true,
      busy: false,
      error: null,
      retry: null,
      actorId: null,
      drafts: { action: "", dialogue: "", ooc: "" },
    });
    try {
      const [snapshot, actions] = await Promise.all([
        this.transport.readSnapshot(campaignId, this.controller.signal),
        this.transport.listActions(campaignId, this.controller.signal),
      ]);
      if (!this.active(g)) return;
      this.patch({
        snapshot,
        loading: false,
        actorId:
          snapshot.characters.find(
            (c) =>
              snapshot.campaign.membership.actor_ids.includes(c.id) &&
              snapshot.scene.visible_actor_ids.includes(c.id),
          )?.id ?? null,
      });
      this.patch({
        entries: actions.map((action) => ({
          id: action.id,
          channel: "action",
          text: "Previously submitted action",
          action,
          narration: null,
        })),
      });
      this.hydrateDrafts();
      for (const action of actions) {
        if (action.status === "submitted" || action.status === "resolving") {
          this.patch({ busy: true });
          await this.followAction(action.id, action, g);
          if (!this.active(g)) return;
          this.patch({ busy: false });
        }
      }
      try {
        sessionStorage.setItem(
          `wayfarer:resume:${this.transport.principalId}`,
          campaignId,
        );
      } catch {
        /* Optional preference. */
      }
    } catch (error) {
      this.fail(error, g);
    }
  }
  resumeId() {
    try {
      const id = sessionStorage.getItem(
        `wayfarer:resume:${this.transport.principalId}`,
      );
      return this.state.campaigns.some((c) => c.id === id) ? id : null;
    } catch {
      return null;
    }
  }
  chooseActor(id: string) {
    if (
      this.state.busy ||
      this.state.retry ||
      this.state.entries.some((e) => e.action?.status === "needs_clarification")
    )
      return;
    const s = this.state.snapshot;
    if (
      s?.campaign.membership.actor_ids.includes(id) &&
      s.scene.visible_actor_ids.includes(id) &&
      s.characters.some((c) => c.id === id)
    ) {
      this.patch({ actorId: id });
      this.hydrateDrafts();
    }
  }
  private fail(error: unknown, g: number) {
    if (!this.active(g)) return;
    if (
      error instanceof TransportError &&
      ["unauthenticated", "forbidden", "not_found"].includes(error.code)
    ) {
      this.expire();
      return;
    }
    this.patch({
      needsRefresh:
        error instanceof TransportError && error.code === "stale_version",
      error: error instanceof Error ? error.message : "The request failed.",
      loading: false,
      busy: false,
    });
  }
  private versions() {
    const s = this.state.snapshot,
      actor = s?.characters.find((c) => c.id === this.state.actorId);
    if (!s || !actor) throw new Error("Select a controlled character first.");
    const inventory = s.inventories.find((i) => i.actor_id === actor.id);
    return {
      scene: s.scene.version,
      character: actor.version,
      ...(inventory ? { inventory: inventory.version } : {}),
    };
  }
  canSend(intentKind: Intent["kind"]) {
    const s = this.state.snapshot;
    return (
      !!s &&
      !this.state.expired &&
      !this.state.needsRefresh &&
      !this.state.loading &&
      !this.state.busy &&
      !this.state.retry &&
      !this.state.entries.some(
        (e) => e.action?.status === "needs_clarification",
      ) &&
      s.campaign.membership.role === "player" &&
      !!this.state.actorId &&
      s.campaign.capabilities.includes(`actions.${intentKind}`)
    );
  }
  async send(channel: Channel, text: string, intent?: Intent) {
    const value =
      intent ??
      (channel === "ooc"
        ? { kind: "question", text: text.trim() }
        : {
            kind: "text",
            text:
              channel === "dialogue"
                ? `My character says: ${text.trim()}`
                : text.trim(),
          });
    if (
      !this.canSend(value.kind) ||
      !text.trim() ||
      ("text" in value && value.text.length > 2000)
    )
      return;
    const entryId = crypto.randomUUID();
    const request: SubmitAction = {
      command_id: crypto.randomUUID(),
      actor_id: this.state.actorId!,
      scene_id: this.state.snapshot!.scene.id,
      expected_versions: this.versions(),
      intent: value,
    };
    this.patch({
      entries: [
        ...this.state.entries,
        { id: entryId, channel, text, action: null, narration: null },
      ],
    });
    await this.execute({
      kind: "submit",
      request,
      entryId,
      clearDraft: !intent,
    });
  }
  inventoryBlockReason(
    itemId: string,
    kind: InventoryOperation,
  ): string | null {
    const s = this.state.snapshot;
    const item = s?.inventories
      .find((i) => i.actor_id === this.state.actorId)
      ?.items.find((i) => i.id === itemId);
    if (!s || !item || !this.state.actorId)
      return "Select a character and item first.";
    if (this.state.expired) return "Your session has ended.";
    if (this.state.needsRefresh)
      return "Reload the changed inventory before trying again.";
    if (this.state.loading || this.state.busy || this.state.retry)
      return "Another action is pending. Finish or retry it first.";
    if (
      this.state.entries.some((e) => e.action?.status === "needs_clarification")
    )
      return "Answer the pending clarification in Play first.";
    if (
      s.campaign.membership.role !== "player" ||
      !s.campaign.membership.actor_ids.includes(this.state.actorId) ||
      !s.scene.visible_actor_ids.includes(this.state.actorId)
    )
      return "You do not control this character in the current scene.";
    const detail = s.inventoryDetails?.[this.state.actorId]?.items[itemId];
    const affordance = detail?.affordances.find((a) => a.kind === kind);
    if (affordance && !affordance.allowed)
      return affordance.reason ?? "This operation is not currently available.";
    if (item.location === "confiscated" && kind !== "inspect")
      return "Confiscated items are not in your custody.";
    if (kind === "inspect" || kind === "use_item") {
      if (
        !item.allowed_actions.includes(kind) ||
        !s.campaign.capabilities.includes(`actions.${kind}`)
      )
        return "This action is not permitted for this item.";
    } else if (
      !this.transport.sample ||
      !this.transport.inventoryPreview ||
      !affordance?.allowed
    )
      return "This operation is awaiting an integrated inventory contract.";
    return null;
  }
  async sendInventory(intent: InventoryIntent) {
    const itemId =
      intent.kind === "inspect" ? intent.target_id : intent.item_id;
    if (this.inventoryBlockReason(itemId, intent.kind)) return;
    const s = this.state.snapshot!,
      inventory = s.inventories.find((i) => i.actor_id === this.state.actorId)!;
    const item = inventory.items.find((i) => i.id === itemId)!;
    if (
      "quantity" in intent &&
      (!Number.isSafeInteger(intent.quantity) ||
        intent.quantity < 1 ||
        intent.quantity > item.quantity)
    )
      return;
    const detail = s.inventoryDetails?.[this.state.actorId!];
    if (
      intent.kind === "equip" &&
      !detail?.slots.some((x) => x.id === intent.slot_id)
    )
      return;
    if (
      intent.kind === "store" &&
      !detail?.containers.some(
        (x) => x.id === intent.container_id && x.accessible,
      )
    )
      return;
    if (
      intent.kind === "transfer" &&
      !detail?.recipients.some(
        (x) => x.id === intent.recipient_actor_id && x.reachable,
      )
    )
      return;
    const text = `${intent.kind.replaceAll("_", " ")} ${item.name}`;
    if (intent.kind === "inspect" || intent.kind === "use_item") {
      await this.send("action", text, intent);
      return;
    }
    const request: InventoryCommand = {
      command_id: crypto.randomUUID(),
      actor_id: this.state.actorId!,
      scene_id: s.scene.id,
      expected_versions: { ...this.versions(), inventory: inventory.version },
      intent,
    };
    const entryId = crypto.randomUUID();
    this.patch({
      entries: [
        ...this.state.entries,
        { id: entryId, channel: "action", text, action: null, narration: null },
      ],
    });
    await this.execute({ kind: "inventory", request, entryId });
  }
  async clarify(entryId: string, answer: ClarifyAction["answer"]) {
    const action = this.state.entries.find((e) => e.id === entryId)?.action;
    if (
      !action ||
      action.status !== "needs_clarification" ||
      this.state.busy ||
      this.state.retry
    )
      return;
    if (
      "text" in answer &&
      (!action.clarification.allows_text ||
        !answer.text.trim() ||
        answer.text.length > 2000)
    )
      return;
    if (
      "choice_id" in answer &&
      !action.clarification.choices.some((c) => c.id === answer.choice_id)
    )
      return;
    const request: ClarifyAction = {
      command_id: crypto.randomUUID(),
      expected_action_version: action.version,
      expected_versions: this.versions(),
      clarification_id: action.clarification.id,
      answer,
    };
    await this.execute({
      kind: "clarify",
      actionId: action.id,
      request,
      entryId,
    });
  }
  async retry() {
    if (this.state.retry && !this.state.busy)
      await this.execute(this.state.retry);
  }
  private async followAction(entryId: string, action: Action, g: number) {
    const campaignId = this.state.selectedId!;
    const signal = this.controller.signal;
    while (action.status === "submitted" || action.status === "resolving") {
      await wait(this.pollMs, signal);
      action = await this.transport.getAction(campaignId, action.id, signal);
      if (!this.active(g)) return;
      this.entry(entryId, { action });
    }
    if (action.status === "succeeded") {
      // Read committed projections rather than applying prose or calculating deltas client-side.
      const snapshot = await this.transport.readSnapshot(campaignId, signal);
      if (!this.active(g)) return;
      this.patch({ snapshot });
      try {
        for await (const narration of this.transport.narrate(
          campaignId,
          action.id,
          signal,
        )) {
          if (!this.active(g)) return;
          this.entry(entryId, { narration });
        }
      } catch (error) {
        if (!this.active(g)) return;
        if (
          error instanceof TransportError &&
          ["unauthenticated", "forbidden", "not_found"].includes(error.code)
        ) {
          this.expire();
          return;
        }
        this.entry(entryId, {
          narration: {
            text: "Narration unavailable. Your committed result is saved.",
            status: "failed",
          },
        });
      }
    }
  }
  private async execute(command: Command) {
    const campaignId = this.state.selectedId;
    if (!campaignId) return;
    const g = this.generation,
      signal = this.controller.signal;
    this.patch({ busy: true, error: null, retry: command });
    try {
      const action =
        command.kind === "submit"
          ? await this.transport.submitAction(
              campaignId,
              command.request,
              signal,
            )
          : command.kind === "inventory"
            ? await this.transport.inventoryPreview!.submitInventory(
                campaignId,
                command.request,
                signal,
              )
            : await this.transport.clarifyAction(
                campaignId,
                command.actionId,
                command.request,
                signal,
              );
      if (!this.active(g)) return;
      this.patch({ retry: null });
      this.entry(command.entryId, { action });
      if (command.kind === "submit" && command.clearDraft) {
        const e = this.state.entries.find((e) => e.id === command.entryId);
        if (e) this.saveDraft(e.channel, "");
      }
      await this.followAction(command.entryId, action, g);
      if (this.active(g)) this.patch({ busy: false });
    } catch (error) {
      if (!this.active(g)) return;
      // Unknown acceptance retries the exact request. Postacceptance read failure also recovers via that receipt.
      if (
        !(error instanceof TransportError) ||
        error.code === "network" ||
        ["internal_error", "service_unavailable", "rate_limited"].includes(
          error.code,
        )
      )
        this.patch({ retry: command });
      else this.patch({ retry: null });
      this.fail(error, g);
    }
  }
}
