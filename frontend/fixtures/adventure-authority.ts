import { randomUUID } from "node:crypto";
import type {
  AdventureView,
  ClosureCommand,
  DecisionCommand,
  Discovery,
  JournalKind,
  SessionClosureView,
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
      settlementReceipt?: string;
      settlementCommand?: string;
      selections: Map<string, string>;
      closureJourney:
        "success" | "partial" | "failure" | "continue" | "archive";
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
        selections: new Map(),
        closureJourney: "continue",
      };
      this.states.set(key, state);
    }
    return state;
  }
  closure(
    who: Identity,
    scope: Scope,
    epoch: string,
    journey?: "success" | "partial" | "failure" | "continue" | "archive",
  ): SessionClosureView {
    const state = this.access(who, scope, epoch);
    if (journey) state.closureJourney = journey;
    const outcome =
      state.closureJourney === "success" || state.closureJourney === "archive"
        ? "success"
        : state.closureJourney === "partial"
          ? "partial"
          : "failure";
    const settled = !!state.settlementReceipt;
    const selectedDowntime = state.selections.get("downtime");
    const objectives: SessionClosureView["objectives"] = [
      {
        id: "escape",
        title: "Open an escape route",
        outcome:
          outcome === "success"
            ? "achieved"
            : outcome === "failure"
              ? "failed"
              : "partial",
        detail:
          outcome === "failure"
            ? "The route remains guarded."
            : "The marked route reaches the alder ford.",
      },
    ];
    if (outcome === "partial")
      objectives.push({
        id: "witness",
        title: "Protect the witness",
        outcome: "partial",
        detail: "Safe passage was promised but is not yet complete.",
      });
    return {
      version: state.version,
      session: {
        status: ["success", "partial", "archive"].includes(state.closureJourney)
          ? "completed"
          : "paused",
        summary:
          outcome === "success"
            ? "The prisoners escaped through the alder ford before dawn."
            : outcome === "partial"
              ? "The route opened, but the witness remains in danger."
              : "The escape failed tonight. The campaign and its consequences continue.",
      },
      campaign: {
        status:
          state.closureJourney === "archive"
            ? "archived"
            : outcome === "success"
              ? "completed"
              : state.closureJourney === "partial"
                ? "active"
                : "paused",
        canContinue:
          state.closureJourney === "partial" ||
          state.closureJourney === "continue" ||
          state.closureJourney === "failure",
      },
      outcome,
      objectives,
      epilogue: [
        outcome === "failure"
          ? "The cell door closes again. Mara keeps the copper finch password secret."
          : "Rain erases the party’s tracks along the marked route.",
      ],
      rewards:
        outcome === "failure"
          ? []
          : [
              {
                id: "earned-1",
                label: "Character points",
                detail: "2 points authorized by the campaign record",
              },
            ],
      consequences:
        outcome === "success"
          ? [
              {
                id: "promise",
                kind: "commitment",
                detail: "Guide the witness to the coast.",
              },
            ]
          : [
              {
                id: "custody",
                kind: "custody",
                detail: "Mara remains in the watch’s custody.",
              },
            ],
      settlement: {
        id: "settlement-escape",
        status: settled ? "settled" : "pending",
        ...(settled ? { settledAt: "2026-09-07T03:00:00Z" } : {}),
      },
      advancement: [
        {
          id: "downtime",
          label: "Downtime",
          options: [
            {
              id: "recover",
              label: "Recover",
              detail: "Treat lasting injuries between adventures.",
            },
            {
              id: "research",
              label: "Research",
              detail: "Follow the known mark on the courier’s seal.",
            },
          ],
          ...(selectedDowntime ? { selectedId: selectedDowntime } : {}),
        },
      ],
      ...(outcome !== "success"
        ? {
            nextAdventure: {
              title: "Beyond the Alder Ford",
              premise:
                "Finish the escape and honor the promise of safe passage.",
              knownHook: "The marked seal points toward a coastal safe house.",
            },
          }
        : {}),
    };
  }
  settle(who: Identity, command: ClosureCommand): SessionClosureView {
    const state = this.access(who, command.scope, command.epoch);
    const serialized = JSON.stringify(command);
    if (state.settlementReceipt) {
      if (
        state.settlementReceipt !== command.commandId ||
        state.settlementCommand !== serialized
      )
        throw new TransportError(
          "illegal_action",
          "Settlement was already claimed.",
        );
      return this.closure(who, command.scope, command.epoch);
    }
    const view = this.closure(who, command.scope, command.epoch);
    if (
      command.version !== view.version ||
      command.settlementId !== view.settlement.id
    )
      throw new TransportError(
        "stale_version",
        "Closure changed. Reload before settling rewards.",
      );
    for (const selection of command.selections) {
      const advancement = view.advancement.find(
        (item) => item.id === selection.advancementId,
      );
      if (
        !advancement?.options.some((option) => option.id === selection.optionId)
      )
        throw new TransportError(
          "illegal_action",
          "Choose an offered advancement option.",
        );
      state.selections.set(selection.advancementId, selection.optionId);
    }
    state.settlementReceipt = command.commandId;
    state.settlementCommand = serialized;
    return this.closure(who, command.scope, command.epoch);
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
