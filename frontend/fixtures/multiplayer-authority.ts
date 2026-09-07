import { randomUUID } from "node:crypto";
import { fixtureAction, fixtureSnapshot } from "../src/play/fixtures";
import {
  TransportError,
  type Action,
  type SubmitAction,
  type ClarifyAction,
} from "../src/play/transport";
import type {
  MultiplayerView,
  Scope,
  ScopeEvent,
  TableCommand,
} from "../src/multiplayer/model";

export type Identity = "captive" | "rescuer";
type Actor = "hero-1" | "hero-2" | "hero-3";
const names = { "hero-1": "Mara", "hero-2": "Ivo", "hero-3": "Sera" };
const token = () => randomUUID().replaceAll("-", "");
/** Development-server-only scripted authority. Never imported by the browser bundle. */
export class MultiplayerAuthority {
  private actors: Record<
    Actor,
    {
      scene: string;
      cursor: string;
      epoch: string;
      version: string;
      actions: Action[];
    }
  > = {
    "hero-1": {
      scene: "cell",
      cursor: token(),
      epoch: token(),
      version: token(),
      actions: [],
    },
    "hero-2": {
      scene: "woods",
      cursor: token(),
      epoch: token(),
      version: token(),
      actions: [],
    },
    "hero-3": {
      scene: "watch",
      cursor: token(),
      epoch: token(),
      version: token(),
      actions: [],
    },
  };
  private control: Record<Identity, Actor[]> = {
    captive: ["hero-1"],
    rescuer: ["hero-2", "hero-3"],
  };
  private membership: Record<Identity, string> = {
    captive: token(),
    rescuer: token(),
  };
  private ready: Record<Identity, boolean> = { captive: false, rescuer: false };
  private seen: Record<Identity, number> = { captive: 0, rescuer: 0 };
  private online: Record<Identity, boolean> = {
    captive: false,
    rescuer: false,
  };
  private receipts = new Map<string, { body: string; value: Action | null }>();
  private ooc: MultiplayerView["ooc"]["messages"] = [];
  private rescue = false;
  constructor(readonly room: string) {}
  principal(identity: Identity) {
    return `mock:${this.room}:${identity}`;
  }
  private owned(identity: Identity, id: string | null): Actor {
    const actor = id ?? this.control[identity][0];
    if (!actor || !this.control[identity].includes(actor as Actor))
      throw new TransportError("forbidden", "Perspective access changed.");
    return actor as Actor;
  }
  private bump(actor: Actor, visibility = false) {
    const state = this.actors[actor];
    state.cursor = token();
    state.version = token();
    if (visibility) state.epoch = token();
  }
  private touch(identity: Identity) {
    this.seen[identity] = Date.now();
    for (const who of ["captive", "rescuer"] as const) {
      const online = Date.now() - this.seen[who] < 6000;
      if (online !== this.online[who]) {
        this.online[who] = online;
        for (const actor of Object.keys(this.actors) as Actor[]) {
          if (
            this.control[who].some(
              (id) => this.actors[id].scene === this.actors[actor].scene,
            )
          )
            this.bump(actor);
        }
      }
    }
  }
  read(identity: Identity, actorId: string | null): MultiplayerView {
    const actor = this.owned(identity, actorId);
    this.touch(identity);
    const state = this.actors[actor];
    const snapshot = fixtureSnapshot("campaign-1");
    snapshot.campaign.membership = {
      ...snapshot.campaign.membership,
      principal_id: this.principal(identity),
      actor_ids: [...this.control[identity]],
      version: this.membership[identity],
    };
    snapshot.campaign.version = state.version;
    snapshot.scene.id = state.scene;
    snapshot.scene.version = state.version;
    const scenes: Record<string, [string, string]> = {
      cell: [
        "The locked cell",
        "Only you know the guard’s password: copper finch.",
      ],
      woods: [
        "The woodland trail",
        "You discovered an unmarked ford beneath the alder trees.",
      ],
      watch: [
        "The old watchpost",
        "Sera can see a clear path to the woodland trail.",
      ],
      reunion: [
        "The rendezvous",
        "Your companions have arrived. Earlier discoveries remain private until someone shares them.",
      ],
    };
    [snapshot.scene.title, snapshot.scene.description] = scenes[state.scene]!;
    snapshot.scene.observations = [];
    snapshot.scene.visible_actor_ids = (
      Object.keys(this.actors) as Actor[]
    ).filter((id) => this.actors[id].scene === state.scene);
    const character = snapshot.characters[0]!;
    character.id = actor;
    character.name = names[actor];
    character.version = state.version;
    snapshot.inventories[0]!.actor_id = actor;
    snapshot.inventories[0]!.version = state.version;
    if (state.scene === "cell") {
      snapshot.inventories[0]!.items = [];
      snapshot.inventories[0]!.total_weight_grams = 0;
    }
    snapshot.session!.summary =
      actor === "hero-1"
        ? "You remember the password copper finch."
        : actor === "hero-2"
          ? "You remember the alder ford."
          : "You reached the watchpost.";
    snapshot.objectives = ["Choose your next step with your companions."];
    snapshot.party = snapshot.scene.visible_actor_ids.map((id) => ({
      id,
      name: names[id as Actor],
      status: "In this scene",
    }));
    const presence = (["captive", "rescuer"] as const)
      .filter((who) =>
        this.control[who].some((id) => this.actors[id].scene === state.scene),
      )
      .map((who) => ({
        id: this.principal(who),
        name: who === "captive" ? "Mara’s player" : "Ivo’s player",
        status: this.online[who] ? ("online" as const) : ("away" as const),
        ready: this.ready[who],
      }));
    const destinations: MultiplayerView["destinations"] = [];
    if (state.scene === "woods")
      destinations.push({
        id: "watch",
        label: "Old watchpost",
        kinds: ["split"],
      });
    if (state.scene === "watch")
      destinations.push({
        id: "woods",
        label: "Woodland trail",
        kinds: ["transfer"],
      });
    if (this.rescue && state.scene !== "reunion")
      destinations.push({
        id: "reunion",
        label: "Rendezvous",
        kinds: ["rejoin"],
      });
    return structuredClone({
      scope: { campaignId: "campaign-1", sceneId: state.scene, actorId: actor },
      checkpoint: { cursor: state.cursor, epoch: state.epoch },
      snapshot,
      actions: state.actions.filter((a) => a.scene_id === state.scene),
      controlledActors: this.control[identity].map((id) => ({
        id,
        name: names[id],
      })),
      presence,
      groupVersion: state.version,
      destinations,
      ooc: { enabled: true, messages: this.ooc },
    });
  }
  events(
    identity: Identity,
    scope: Scope,
    cursor: string,
    epoch: string,
  ): ScopeEvent | null {
    if (!this.control[identity].includes(scope.actorId as Actor))
      return { kind: "revoked" };
    const view = this.read(identity, scope.actorId);
    if (view.scope.sceneId !== scope.sceneId || view.checkpoint.epoch !== epoch)
      return { kind: "reset" };
    return view.checkpoint.cursor === cursor
      ? null
      : {
          kind: "changed",
          scope,
          previousCursor: cursor,
          checkpoint: view.checkpoint,
        };
  }
  private receipt(identity: Identity, id: string, body: unknown) {
    const key = `${identity}:${id}`,
      old = this.receipts.get(key);
    if (old && old.body !== JSON.stringify(body))
      throw new TransportError(
        "idempotency_conflict",
        "Command identity was reused with different content.",
      );
    return old;
  }
  submit(identity: Identity, command: SubmitAction): Action {
    const actor = this.owned(identity, command.actor_id),
      state = this.actors[actor];
    const old = this.receipt(identity, command.command_id, command);
    if (old?.value) return structuredClone(old.value);
    if (
      state.scene !== command.scene_id ||
      state.version !== command.expected_versions.scene ||
      state.version !== command.expected_versions.character
    )
      throw new TransportError(
        "stale_version",
        "The scene changed. Reload and review your action.",
      );
    if (
      state.actions.some(
        (a) => a.scene_id === state.scene && a.status === "needs_clarification",
      )
    )
      throw new TransportError(
        "illegal_action",
        "Answer your scene’s pending choice first.",
      );
    const action = fixtureAction(
      command,
      randomUUID(),
      state.scene === "cell" ? "needs_clarification" : "succeeded",
    );
    if (action.status === "needs_clarification") {
      action.clarification.prompt = "Wait for the guard, or examine the lock?";
      action.clarification.choices = [
        { id: "wait", label: "Wait for the guard" },
      ];
    }
    if (action.status === "succeeded")
      action.resolution = {
        summary: "Your scene’s action is complete.",
        checks: [],
        changed_resources: [],
        game_time: { ticks: 12, tick_duration_ms: 1000 },
      };
    state.actions.push(action);
    this.receipts.set(`${identity}:${command.command_id}`, {
      body: JSON.stringify(command),
      value: action,
    });
    this.bump(actor);
    return structuredClone(action);
  }
  clarify(
    identity: Identity,
    actorId: string,
    id: string,
    command: ClarifyAction,
  ): Action {
    const actor = this.owned(identity, actorId),
      state = this.actors[actor];
    const old = this.receipt(identity, command.command_id, command);
    if (old?.value) return structuredClone(old.value);
    const index = state.actions.findIndex(
        (a) => a.id === id && a.scene_id === state.scene,
      ),
      action = state.actions[index];
    if (!action || action.status !== "needs_clarification")
      throw new TransportError("not_found", "Choice unavailable.");
    if (
      action.version !== command.expected_action_version ||
      command.clarification_id !== action.clarification.id ||
      command.expected_versions.scene !== state.version
    )
      throw new TransportError(
        "stale_version",
        "Choice changed. Reload the scene.",
      );
    if (
      ("choice_id" in command.answer && command.answer.choice_id !== "wait") ||
      ("text" in command.answer && !command.answer.text.trim())
    )
      throw new TransportError("illegal_action", "Choose a permitted answer.");
    const result: Action = {
      ...action,
      status: "succeeded",
      version: token(),
      resolution: {
        summary:
          "You wait for the guard. Your companions can continue independently.",
        checks: [],
        changed_resources: [],
        game_time: { ticks: 12, tick_duration_ms: 1000 },
      },
    };
    // Avoid serializing fields from another discriminator variant.
    const { clarification: _choice, ...base } = action;
    void _choice;
    state.actions[index] = {
      ...base,
      status: "succeeded",
      version: result.version,
      resolution: result.resolution,
    };
    this.receipts.set(`${identity}:${command.command_id}`, {
      body: JSON.stringify(command),
      value: state.actions[index]!,
    });
    this.bump(actor);
    return structuredClone(state.actions[index]!);
  }
  command(identity: Identity, command: TableCommand) {
    const actor = this.owned(identity, command.scope.actorId),
      state = this.actors[actor];
    if (this.receipt(identity, command.commandId, command)) return;
    if (
      command.scope.campaignId !== "campaign-1" ||
      state.scene !== command.scope.sceneId ||
      command.expectedGroupVersion !== state.version ||
      command.expectedMembershipVersion !== this.membership[identity]
    )
      throw new TransportError(
        "stale_version",
        "Membership or scene changed. Refresh before trying again.",
      );
    const intent = command.intent;
    const previousScene = state.scene;
    if (intent.kind === "ready") this.ready[identity] = intent.ready;
    else if (intent.kind === "ooc") {
      if (!intent.text.trim() || intent.text.length > 2000)
        throw new TransportError(
          "illegal_action",
          "Enter a message up to 2000 characters.",
        );
      this.ooc.push({
        id: command.commandId,
        author: identity === "captive" ? "Mara’s player" : "Ivo’s player",
        text: intent.text,
      });
    } else {
      if (
        state.actions.some(
          (a) =>
            a.scene_id === state.scene && a.status === "needs_clarification",
        )
      )
        throw new TransportError(
          "illegal_action",
          "Resolve your scene’s pending choice before moving.",
        );
      if (
        !this.read(identity, actor).destinations.some(
          (d) => d.id === intent.destinationId && d.kinds.includes(intent.kind),
        )
      )
        throw new TransportError(
          "illegal_action",
          "Destination is not reachable.",
        );
      state.scene = intent.destinationId;
      this.bump(actor, true);
    }
    this.receipts.set(`${identity}:${command.commandId}`, {
      body: JSON.stringify(command),
      value: null,
    });
    for (const id of Object.keys(this.actors) as Actor[])
      if (
        intent.kind === "ooc" ||
        this.actors[id].scene === state.scene ||
        this.actors[id].scene === previousScene
      )
        this.bump(id);
  }
  /** Explicit test-driver events, no game rules inferred from prose. */
  scenario(
    kind: "rescue" | "revoke" | "reassign" | "missed",
    identity: Identity,
  ) {
    if (kind === "rescue") {
      this.rescue = true;
      for (const id of Object.keys(this.actors) as Actor[]) this.bump(id);
    }
    if (kind === "revoke" || kind === "reassign") {
      this.control[identity] = [];
      this.membership[identity] = token();
    }
    if (kind === "missed")
      for (const id of this.control[identity]) this.bump(id);
  }
}
