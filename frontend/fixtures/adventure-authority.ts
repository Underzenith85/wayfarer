import { randomUUID } from "node:crypto";
import type {
  AdventureView,
  DecisionCommand,
  Discovery,
  JournalKind,
} from "../src/adventure/model";
import type { Scope } from "../src/multiplayer/model";
import { sameScope } from "../src/multiplayer/model";
import { TransportError } from "../src/play/transport";
import type { Identity, MultiplayerAuthority } from "./multiplayer-authority";

/** Server-only knowledge and rules fixture. No hidden records are shipped to the browser. */
export class AdventureAuthority {
  private states = new Map<
    string,
    {
      version: string;
      step: number;
      receipts: Map<string, string>;
      checkpoints: Map<string, number>;
    }
  >();
  constructor(private table: MultiplayerAuthority) {}
  private access(who: Identity, scope: Scope, epoch: string) {
    const view = this.table.read(who, scope.actorId);
    if (!sameScope(scope, view.scope) || epoch !== view.checkpoint.epoch)
      throw new TransportError(
        "stale_version",
        "Perspective changed. Reload before continuing.",
      );
    const key = `${who}:${scope.actorId}:${scope.sceneId}:${epoch}`;
    let state = this.states.get(key);
    if (!state) {
      state = {
        version: randomUUID(),
        step: 0,
        receipts: new Map(),
        checkpoints: new Map(),
      };
      this.states.set(key, state);
    }
    return state;
  }
  overview(
    who: Identity,
    scope: Scope,
    epoch: string,
    since: string | null,
  ): AdventureView {
    const state = this.access(who, scope, epoch);
    const modes = [
      "combat",
      "social",
      "investigation",
      "stealth",
      "hazard",
    ] as const;
    const mode = modes[state.step];
    const labels = [
      "Raise shield",
      "Offer safe passage",
      "Examine the seal",
      "Move through cover",
      "Secure the rope",
    ];
    const prompts = [
      "A guard strikes. Choose your defense.",
      "The witness asks for a promise.",
      "A marked seal may reveal the route.",
      "Cross the watched courtyard.",
      "Make the broken bridge safe.",
    ];
    const changes = [
      "Deflected the guard’s strike.",
      "Promised the witness safe passage.",
      "Discovered the marked route.",
      "Crossed the courtyard unseen.",
      "Secured the bridge. Escape route complete.",
    ];
    const start = since ? state.checkpoints.get(since) : 0;
    const checkpoint = state.version;
    state.checkpoints.set(checkpoint, state.step);
    return {
      encounter: mode
        ? {
            id: `encounter-${scope.actorId}`,
            version: state.version,
            round: state.step + 1,
            activeActor: scope.actorId,
            mode,
            prompt: prompts[state.step]!,
            decisionId: `decision-${state.step}`,
            choices: [
              {
                id: `choice-${state.step}`,
                label: labels[state.step]!,
                trace:
                  mode === "combat"
                    ? "Shield ready · defense permitted against the visible guard · success absorbs the strike."
                    : "Known approach · permitted by the current decision · success advances the escape objective.",
                targets: [
                  {
                    id: mode === "combat" ? "guard" : "route",
                    name: mode === "combat" ? "Visible guard" : "Escape route",
                    range: mode === "combat" ? "Within reach" : "In this scene",
                  },
                ],
              },
            ],
          }
        : null,
      objectives: [
        {
          id: "escape",
          title: "Open an escape route",
          progress: `${state.step} of 5 known steps`,
          status: state.step === 5 ? "complete" : "active",
        },
      ],
      recap: {
        checkpoint,
        reset: !!since && start === undefined,
        changes: changes.slice(start ?? 0, state.step),
      },
    };
  }
  search(
    who: Identity,
    scope: Scope,
    epoch: string,
    query: string,
    kind: JournalKind | "all",
  ): Discovery[] {
    const state = this.access(who, scope, epoch);
    // Records are selected by server-owned knowledge before search, counts or snippets.
    const entries: Discovery[] = [
      {
        id: `witness-${scope.actorId}`,
        kind: "npc",
        title: "The witness",
        text: "A traveler waiting for safe passage.",
        learnedAt: "Day 1, dusk",
      },
      {
        id: `place-${scope.actorId}`,
        kind: "location",
        title: "Current refuge",
        text:
          scope.actorId === "hero-1"
            ? "The cell password is copper finch."
            : "The alder ford leads toward shelter.",
        learnedAt: "Day 1, dusk",
      },
      ...(state.step >= 2
        ? [
            {
              id: `promise-${scope.actorId}`,
              kind: "commitment" as const,
              title: "Safe passage",
              text: "You promised to guide the witness to safety.",
              learnedAt: "Day 1, evening",
            },
          ]
        : []),
      ...(state.step >= 3
        ? [
            {
              id: `route-${scope.actorId}`,
              kind: "clue" as const,
              title: "Marked route",
              text: "The seal marks a sheltered escape route.",
              learnedAt: "Day 1, evening",
            },
          ]
        : []),
    ];
    return entries
      .filter(
        (e) =>
          (kind === "all" || e.kind === kind) &&
          `${e.title} ${e.text}`.toLowerCase().includes(query.toLowerCase()),
      )
      .slice(0, 50);
  }
  entry(who: Identity, scope: Scope, epoch: string, id: string) {
    const entry = this.search(who, scope, epoch, "", "all").find(
      (e) => e.id === id,
    );
    if (!entry)
      throw new TransportError("not_found", "Journal entry unavailable.");
    return entry;
  }
  decide(who: Identity, command: DecisionCommand) {
    const state = this.access(who, command.scope, command.epoch);
    const body = JSON.stringify(command);
    const receipt = state.receipts.get(command.commandId);
    if (receipt) {
      if (receipt !== body)
        throw new TransportError("illegal_action", "Command ID already used.");
      return;
    }
    const encounter = this.overview(
      who,
      command.scope,
      command.epoch,
      null,
    ).encounter;
    if (
      !encounter ||
      command.version !== encounter.version ||
      command.decisionId !== encounter.decisionId
    )
      throw new TransportError(
        "stale_version",
        "Decision changed. Reload the encounter.",
      );
    const choice = encounter.choices.find((c) => c.id === command.choiceId);
    if (!choice || !choice.targets.some((t) => t.id === command.targetId))
      throw new TransportError(
        "illegal_action",
        "Choose a permitted option and target.",
      );
    if (
      this.table
        .read(who, command.scope.actorId)
        .actions.some((a) =>
          ["submitted", "resolving", "needs_clarification"].includes(a.status),
        )
    )
      throw new TransportError(
        "illegal_action",
        "Finish the pending action first.",
      );
    state.receipts.set(command.commandId, body);
    state.step += 1;
    state.version = randomUUID();
  }
}
