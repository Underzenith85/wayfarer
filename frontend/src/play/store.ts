import {
  sameScope,
  scopeKey,
  type MultiplayerView,
  type TableCommand,
  type TableIntent,
} from "../multiplayer/model";
import type {
  InventoryCommand,
  InventoryIntent,
  InventoryOperation,
} from "../character/presentation";
import { forgetSession } from "./session";
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
  lastSynchronized: string | null;
  connection: "online" | "offline" | "recovering";
  multiplayer: MultiplayerView | null;
  tableRetry: TableCommand | null;
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
  lastSynchronized: null,
  connection: "online",
  multiplayer: null,
  tableRetry: null,
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
  private watchSerial = 0;
  constructor(
    readonly transport: PlayTransport,
    private clearCache: () => void = () => {},
    private pollMs = 1000,
    private cacheView: (
      key: readonly string[],
      view: MultiplayerView,
    ) => void = () => {},
  ) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private patch(value: Partial<PlayState>) {
    if (value.snapshot !== undefined)
      value.lastSynchronized = value.snapshot ? new Date().toISOString() : null;
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
    this.encounterRetry = null;
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
    forgetSession();
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
  async select(
    campaignId: string,
    actorId: string | null = null,
    recover: Command | null = null,
  ) {
    this.fence();
    const g = this.generation;
    this.patch({
      snapshot: null,
      multiplayer: null,
      tableRetry: null,
      connection: this.transport.multiplayer
        ? "recovering"
        : this.state.connection,
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
      const view = this.transport.multiplayer
        ? await this.transport.multiplayer.read(
            campaignId,
            actorId,
            this.controller.signal,
          )
        : null;
      const [snapshot, actions] = view
        ? [view.snapshot, view.actions]
        : await Promise.all([
            this.transport.readSnapshot(campaignId, this.controller.signal),
            this.transport.listActions(campaignId, this.controller.signal),
          ]);
      if (!this.active(g)) return;
      if (
        view &&
        (snapshot.campaign.membership.principal_id !==
          this.transport.principalId ||
          view.scope.campaignId !== campaignId ||
          view.scope.sceneId !== snapshot.scene.id ||
          !snapshot.campaign.membership.actor_ids.includes(
            view.scope.actorId,
          ) ||
          !snapshot.scene.visible_actor_ids.includes(view.scope.actorId) ||
          actions.some(
            (a) =>
              a.scene_id !== view.scope.sceneId ||
              a.actor_id !== view.scope.actorId,
          ))
      )
        throw new TransportError("forbidden", "Perspective access changed.");
      this.patch({
        snapshot,
        multiplayer: view,
        connection: "online",
        loading: false,
        actorId:
          view?.scope.actorId ??
          snapshot.characters.find(
            (c) =>
              snapshot.campaign.membership.actor_ids.includes(c.id) &&
              snapshot.scene.visible_actor_ids.includes(c.id),
          )?.id ??
          null,
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
      if (view)
        this.cacheView(
          scopeKey(
            this.transport.principalId,
            view.scope,
            view.checkpoint.epoch,
          ),
          view,
        );
      if (
        recover &&
        view &&
        recover.request.command_id &&
        ((recover.kind === "clarify" &&
          actions.some((a) => a.id === recover.actionId)) ||
          (recover.kind !== "clarify" &&
            recover.request.actor_id === view.scope.actorId &&
            recover.request.scene_id === view.scope.sceneId)) &&
        !actions.some(
          (a) =>
            a.command_id === recover.request.command_id ||
            (recover.kind === "clarify" &&
              a.id === recover.actionId &&
              a.version !== recover.request.expected_action_version),
        )
      ) {
        this.patch({
          retry: recover,
          entries: [
            ...this.state.entries,
            {
              id: recover.entryId,
              channel: "action",
              text: "Request awaiting acknowledgement",
              action: null,
              narration: null,
            },
          ],
        });
      }
      if (view) this.watchMultiplayer(view, g);
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
  async refresh() {
    const id = this.state.selectedId,
      g = this.generation;
    if (!id || this.state.busy) return;
    try {
      const snapshot = await this.transport.readSnapshot(
        id,
        this.controller.signal,
      );
      if (this.active(g)) this.patch({ snapshot });
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
    if (this.transport.multiplayer) {
      if (
        this.state.connection === "online" &&
        !this.state.busy &&
        !this.state.retry &&
        !this.state.tableRetry &&
        this.state.multiplayer?.controlledActors.some((a) => a.id === id)
      )
        void this.select(this.state.selectedId!, id);
      return;
    }
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
    if (
      this.transport.multiplayer &&
      error instanceof TransportError &&
      error.code === "network"
    )
      this.disconnect();
    this.patch({
      needsRefresh:
        error instanceof TransportError && error.code === "stale_version",
      error: error instanceof Error ? error.message : "The request failed.",
      loading: false,
      busy: false,
    });
  }
  disconnect() {
    if (this.state.expired) return;
    this.fence();
    this.patch({ connection: "offline", busy: false, loading: false });
  }
  async reconnect() {
    if (this.state.expired) return;
    if (this.state.selectedId) {
      const tableRetry = this.state.tableRetry;
      const selection = this.select(
        this.state.selectedId,
        this.state.actorId,
        this.state.retry,
      );
      const generation = this.generation;
      await selection;
      if (
        this.active(generation) &&
        tableRetry &&
        this.state.multiplayer &&
        sameScope(tableRetry.scope, this.state.multiplayer.scope)
      )
        this.patch({ tableRetry });
    } else {
      this.patch({ connection: "online" });
      await this.loadCampaigns();
    }
  }
  private watchMultiplayer(view: MultiplayerView, g: number) {
    const serial = ++this.watchSerial;
    const receive = (event: import("../multiplayer/model").ScopeEvent) => {
      if (!this.active(g) || serial !== this.watchSerial) return;
      if (event.kind !== "changed") {
        if (event.kind === "revoked") this.expire();
        else if (event.kind === "disconnected") this.disconnect();
        else void this.reconnect();
        return;
      }
      if (!sameScope(event.scope, view.scope)) return;
      if (event.checkpoint.epoch !== view.checkpoint.epoch) {
        void this.reconnect();
        return;
      }
      if (event.checkpoint.cursor === view.checkpoint.cursor) return;
      if (this.state.busy) {
        void wait(this.pollMs, this.controller.signal)
          .then(() => receive(event))
          .catch(() => {});
        return;
      }
      const ticket = ++this.watchSerial;
      void (async () => {
        try {
          const next = await this.transport.multiplayer!.read(
            view.scope.campaignId,
            view.scope.actorId,
            this.controller.signal,
          );
          if (!this.active(g) || ticket !== this.watchSerial) return;
          if (this.state.busy) {
            this.watchMultiplayer(view, g);
            return;
          }
          if (
            !sameScope(next.scope, view.scope) ||
            next.checkpoint.epoch !== view.checkpoint.epoch
          ) {
            void this.reconnect();
            return;
          }
          if (
            next.snapshot.campaign.membership.principal_id !==
              this.transport.principalId ||
            !next.snapshot.campaign.membership.actor_ids.includes(
              next.scope.actorId,
            ) ||
            next.snapshot.scene.id !== next.scope.sceneId ||
            next.actions.some(
              (a) =>
                a.actor_id !== next.scope.actorId ||
                a.scene_id !== next.scope.sceneId,
            )
          ) {
            this.expire();
            return;
          }
          this.clearCache();
          this.cacheView(
            scopeKey(
              this.transport.principalId,
              next.scope,
              next.checkpoint.epoch,
            ),
            next,
          );
          // Replace authorized data atomically without unmounting controls during a click.
          // Cursors/versions are opaque: even reordered or gapped events reread current state.
          const entries = next.actions.map((action) => {
            const old = this.state.entries.find(
              (e) => e.action?.id === action.id,
            );
            return old
              ? { ...old, action }
              : {
                  id: action.id,
                  channel: "action" as const,
                  text: "Previously submitted action",
                  action,
                  narration: null,
                };
          });
          this.patch({
            snapshot: next.snapshot,
            multiplayer: next,
            entries: [
              ...entries,
              ...this.state.entries.filter((e) => !e.action),
            ],
          });
          this.watchMultiplayer(next, g);
        } catch (error) {
          this.fail(error, g);
        }
      })();
    };
    this.transport.multiplayer!.watch(view, this.controller.signal, receive);
  }
  async tableCommand(intent: TableIntent) {
    const view = this.state.multiplayer;
    if (
      !view ||
      this.state.connection !== "online" ||
      this.state.busy ||
      this.state.retry ||
      this.state.tableRetry ||
      this.state.needsRefresh
    )
      return;
    if (
      intent.kind === "ooc" &&
      (!view.ooc.enabled || !intent.text.trim() || intent.text.length > 2000)
    )
      return;
    if (
      intent.kind !== "ready" &&
      intent.kind !== "ooc" &&
      !view.destinations.some(
        (d) => d.id === intent.destinationId && d.kinds.includes(intent.kind),
      )
    )
      return;
    await this.executeTable({
      commandId: crypto.randomUUID(),
      scope: view.scope,
      expectedGroupVersion: view.groupVersion,
      expectedMembershipVersion: view.snapshot.campaign.membership.version,
      intent,
    });
  }
  async retryTable() {
    if (
      this.state.tableRetry &&
      this.state.connection === "online" &&
      !this.state.busy
    )
      await this.executeTable(this.state.tableRetry);
  }
  private async executeTable(command: TableCommand) {
    if (!this.transport.multiplayer) return;
    const g = this.generation;
    this.patch({ busy: true, tableRetry: command, error: null });
    try {
      await this.transport.multiplayer.command(command, this.controller.signal);
      if (!this.active(g)) return;
      if (command.intent.kind === "ooc") this.saveDraft("ooc", "");
      this.patch({ tableRetry: null });
      await this.select(command.scope.campaignId, command.scope.actorId);
    } catch (error) {
      if (!this.active(g)) return;
      if (
        error instanceof TransportError &&
        error.code !== "network" &&
        error.code !== "service_unavailable"
      )
        this.patch({ tableRetry: null });
      this.fail(error, g);
    }
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
  encounterRetry: import("../adventure/model").DecisionCommand | null = null;
  async decideEncounter(command: import("../adventure/model").DecisionCommand) {
    const view = this.state.multiplayer;
    if (
      !this.transport.adventure ||
      !view ||
      !this.canSend("text", true) ||
      !sameScope(command.scope, view.scope) ||
      command.epoch !== view.checkpoint.epoch
    )
      return;
    const generation = this.generation;
    this.patch({ busy: true });
    this.encounterRetry = command;
    try {
      await this.transport.adventure.decide(command, this.controller.signal);
      if (this.active(generation)) this.encounterRetry = null;
    } catch (error) {
      if (
        this.active(generation) &&
        !(error instanceof TransportError && error.code === "network")
      )
        this.encounterRetry = null;
      throw error;
    } finally {
      if (this.active(generation)) this.patch({ busy: false });
    }
  }
  canSend(intentKind: Intent["kind"], allowEncounterRetry = false) {
    const s = this.state.snapshot;
    return (
      !!s &&
      s.campaign.status === "active" &&
      (allowEncounterRetry || !this.encounterRetry) &&
      this.state.connection === "online" &&
      !this.state.tableRetry &&
      !this.state.expired &&
      !this.state.needsRefresh &&
      !this.state.loading &&
      !this.state.busy &&
      !this.state.retry &&
      !this.state.entries.some((e) =>
        ["submitted", "resolving", "needs_clarification"].includes(
          e.action?.status ?? "",
        ),
      ) &&
      s.campaign.membership.role === "player" &&
      !!this.state.actorId &&
      s.campaign.capabilities.includes(`actions.${intentKind}`)
    );
  }
  async send(
    channel: Channel,
    text: string,
    intent?: Intent,
    options: { preserveDraft?: boolean } = {},
  ) {
    if (this.transport.multiplayer && channel === "ooc") return;
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
      clearDraft: !intent && !options.preserveDraft,
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
    if (this.state.connection !== "online")
      return "Reconnect and reconcile this scene before acting.";
    if (this.state.tableRetry)
      return "Resolve the pending table request first.";
    if (this.state.expired) return "Your session has ended.";
    if (this.state.needsRefresh)
      return "Reload the changed inventory before trying again.";
    if (
      this.state.loading ||
      this.state.busy ||
      this.state.retry ||
      this.state.entries.some(
        (e) =>
          e.action?.status === "submitted" || e.action?.status === "resolving",
      )
    )
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
      this.state.connection !== "online" ||
      this.state.tableRetry ||
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
    if (
      this.state.connection === "online" &&
      this.state.retry &&
      !this.state.busy
    )
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
    if (this.transport.multiplayer) {
      // Clarification receipts can also advance scene versions. Refresh before
      // enabling the answer controls, not just after successful game changes.
      await this.select(campaignId, this.state.actorId);
      return;
    }
    if (action.status === "succeeded") {
      // Read committed projections rather than applying prose or calculating deltas client-side.
      const snapshot = await this.transport.readSnapshot(campaignId, signal);
      if (!this.active(g)) return;
      this.patch({ snapshot });
      // Narration is subscribed to the action's original scene. A successful
      // journey changes that scope; show the destination and finish the action
      // without waiting for narration on an inaccessible old scene.
      if (snapshot.scene.id !== action.scene_id) return;
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
    if (!campaignId || this.state.connection !== "online") return;
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
