import { http, HttpResponse, delay, ws } from "msw";
import type { components } from "../api/contracts.generated";
import type { ServerMessage, Scope } from "../api/events.generated";
import { operations, type OperationId } from "../api/operations.generated";
import {
  parseHttp,
  parseClientMessage,
  parseServerMessage,
} from "../api/validation";
import type { Action, SubmitAction } from "../play/transport";
import {
  scenarioAction,
  scenarioSnapshot,
  fixtureCredential,
  fixtureCommand,
  campaigns,
  type Scenario,
} from "./catalog";
type Schemas = components["schemas"];
const endpoints = new Map<string, ReturnType<typeof ws.link>>();
const stamp = "2026-09-06T22:00:00Z";
const requestId = fixtureCommand.command_id;
const cursor = (n: number) => `fixture_cursor_${n}`.padEnd(40, "_");
const canonical = (value: unknown): string =>
  JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(
          Object.entries(v).sort(([a], [b]) => a.localeCompare(b)),
        )
      : v,
  );
interface RecordAction {
  request: SubmitAction;
  campaignId: string;
  action: Action;
  step: number;
  clarified: boolean;
}
interface Receipt {
  fingerprint: string;
  actionId?: string;
  value?: unknown;
}
class Session {
  actions = new Map<string, RecordAction>();
  receipts = new Map<string, Receipt>();
  settled = new Set<string>();
  revisions = new Map<string, number>();
  checkpoints = new Map<
    string,
    { before: string; after: string; eventId: string }
  >();
  bump(campaignId: string) {
    this.revisions.set(campaignId, (this.revisions.get(campaignId) ?? 0) + 1);
  }
  failedOnce = false;
  revoked = false;
  snapshot(id: string, scenario: Scenario, principal: string) {
    return scenarioSnapshot(scenario, id, this.settled.has(id), principal);
  }
}
export interface MockOptions {
  scenario?: Scenario;
  latency?: number;
  origin: string;
}
/** Stateful protocol receipts around fixed scenario outputs; not a rules engine. */
export function createMockHandlers({
  scenario = "resolve",
  latency = 30,
  origin,
}: MockOptions) {
  let serial = 0;
  const nextId = () =>
    `00000000-0000-4000-8000-${(++serial).toString(16).padStart(12, "0")}`;
  const sessions = new Map<string, Session>();
  const session = (id: string) => {
    let s = sessions.get(id);
    if (!s) {
      s = new Session();
      sessions.set(id, s);
    }
    return s;
  };
  const identify = (token: string | null) =>
    ["player-1", "player-2", "gm"].find(
      (id) => token === `Bearer ${fixtureCredential(id)}`,
    );
  const error = (
    code: Schemas["Error"]["code"],
    message = code.replaceAll("_", " "),
  ): Schemas["Error"] => ({
    code,
    message,
    request_id: requestId,
    retryable: code === "rate_limited" || code === "service_unavailable",
    field_errors: [],
  });
  const reply = (op: OperationId, status: number, value: unknown) => {
    const schema = (operations[op].responses as Record<string, keyof Schemas>)[
      String(status)
    ];
    if (!schema) throw new Error(`Undocumented mock response ${op}:${status}`);
    parseHttp(schema, value);
    return new HttpResponse(JSON.stringify(value), {
      status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Request-ID": requestId,
        ...(status === 401 ? { "WWW-Authenticate": "Bearer" } : {}),
        ...(status === 429 || status === 503 ? { "Retry-After": "1" } : {}),
      },
    });
  };
  const constructors = { GET: http.get, POST: http.post };
  const handlers = Object.entries(operations).map(([key, operation]) =>
    constructors[operation.method](
      `${origin}/api/v1${operation.path.replaceAll("{", ":").replaceAll("}", "")}`,
      async ({ request, params }) => {
        const op = key as OperationId;
        await delay(latency);
        const principal = identify(request.headers.get("Authorization"));
        if (!principal) return reply(op, 401, error("unauthenticated"));
        const state = session(principal);
        if (state.revoked) return reply(op, 404, error("not_found"));
        const campaignId = String(params.campaign_id ?? "campaign-1");
        if (!campaigns.some((x) => x.id === campaignId))
          return reply(op, 404, error("not_found"));
        const snapshot = state.snapshot(campaignId, scenario, principal);
        const actor = snapshot.characters[0]!;
        let body: Record<string, unknown> = {};
        if (operation.requestSchema) {
          try {
            if (
              request.headers.get("Content-Type")?.split(";")[0] !==
              "application/json"
            )
              return reply(op, 415, error("unsupported_media_type"));
            const text = await request.text();
            if (new TextEncoder().encode(text).length > 32000)
              return reply(op, 413, error("payload_too_large"));
            body = JSON.parse(text) as Record<string, unknown>;
            parseHttp(operation.requestSchema, body);
          } catch {
            return reply(op, 400, error("invalid_request"));
          }
        }
        const page = (items: unknown[]) => {
          const url = new URL(request.url);
          const limit = Number(url.searchParams.get("limit") ?? 50);
          if (!Number.isInteger(limit) || limit < 1 || limit > 100)
            return reply(op, 400, error("invalid_request"));
          const supplied = url.searchParams.get("cursor");
          // Fixture cursors are server-side opaque handles bound to this exact principal/operation.
          const binding = `${principal}:${op}:${campaignId}`;
          const receipt = supplied ? state.receipts.get(supplied) : undefined;
          if (supplied && (!receipt || receipt.fingerprint !== binding))
            return reply(op, 400, error("invalid_cursor"));
          const start = receipt ? Number(receipt.value) : 0;
          const selected = items.slice(start, start + limit);
          const next =
            start + limit < items.length
              ? `fixture_page_${principal}_${op}_${start + limit}`.padEnd(
                  40,
                  "_",
                )
              : null;
          if (next)
            state.receipts.set(next, {
              fingerprint: binding,
              value: start + limit,
            });
          return reply(op, 200, { items: selected, next_cursor: next });
        };
        if (op === "getMe")
          return reply(op, 200, { id: principal, display_name: principal });
        if (op === "listCampaigns")
          return page(
            campaigns.map(
              (x) => state.snapshot(x.id, scenario, principal).campaign,
            ),
          );
        if (op === "getCampaign") return reply(op, 200, snapshot.campaign);
        if (op === "listMembers") return page([snapshot.campaign.membership]);
        if (op === "getCurrentSession")
          return snapshot.session
            ? reply(op, 200, snapshot.session)
            : reply(op, 404, error("not_found"));
        if (op === "listCharacters") return page(snapshot.characters);
        if (op === "getCharacter" || op === "getInventory") {
          if (params.actor_id !== actor.id)
            return reply(op, 404, error("not_found"));
          return reply(
            op,
            200,
            op === "getCharacter" ? actor : snapshot.inventories[0],
          );
        }
        if (op === "listScenes") return page([snapshot.scene]);
        if (op === "getScene")
          return params.scene_id === snapshot.scene.id
            ? reply(op, 200, snapshot.scene)
            : reply(op, 404, error("not_found"));
        if (op === "listActions") {
          if (
            new URL(request.url).searchParams.get("scene_id") !==
            snapshot.scene.id
          )
            return reply(op, 404, error("not_found"));
          return page(
            [...state.actions.values()]
              .filter((x) => x.campaignId === campaignId)
              .map((x) => x.action),
          );
        }
        if (op === "getAction") {
          const rec = state.actions.get(String(params.action_id));
          if (!rec || rec.campaignId !== campaignId)
            return reply(op, 404, error("not_found"));
          if (
            !["succeeded", "rejected", "cancelled"].includes(rec.action.status)
          ) {
            rec.step++;
            const status =
              (scenario === "clarify" || scenario === "encounter") &&
              !rec.clarified
                ? "needs_clarification"
                : rec.step === 1
                  ? "resolving"
                  : scenario === "reject" || scenario === "illegal-character"
                    ? "rejected"
                    : "succeeded";
            if (rec.action.status !== status) state.bump(campaignId);
            rec.action = scenarioAction(
              scenario,
              rec.request,
              rec.action.id,
              status,
            );
            if (status === "succeeded") state.settled.add(campaignId);
          }
          return reply(op, 200, rec.action);
        }
        if (
          (op === "submitAction" || op === "clarifyAction") &&
          scenario === "expired"
        )
          return reply(op, 401, error("unauthenticated"));
        const id = String(body.command_id);
        const fingerprint = canonical({
          method: request.method,
          path: new URL(request.url).pathname,
          body,
        });
        const old = state.receipts.get(id);
        if (old) {
          if (old.fingerprint !== fingerprint)
            return reply(op, 409, error("idempotency_conflict"));
          const value = old.actionId
            ? state.actions.get(old.actionId)!.action
            : old.value;
          return reply(
            op,
            op === "createInvitation"
              ? 201
              : op === "submitAction" || op === "clarifyAction"
                ? 202
                : 200,
            value,
          );
        }
        if (op === "submitAction") {
          const command = parseHttp("SubmitAction", body);
          if (principal === "gm") return reply(op, 403, error("forbidden"));
          if (
            command.actor_id !== actor.id ||
            command.scene_id !== snapshot.scene.id
          )
            return reply(op, 404, error("not_found"));
          if (
            scenario === "stale" ||
            command.expected_versions.scene !== snapshot.scene.version ||
            command.expected_versions.character !== actor.version ||
            (command.expected_versions.inventory !== undefined &&
              command.expected_versions.inventory !==
                snapshot.inventories[0]!.version)
          )
            return reply(op, 409, error("stale_version"));
          const aid = `action-${state.actions.size + 1}`;
          const action = scenarioAction(scenario, command, aid, "submitted");
          state.actions.set(aid, {
            request: command,
            campaignId,
            action,
            step: 0,
            clarified: false,
          });
          state.bump(campaignId);
          state.receipts.set(id, { fingerprint, actionId: aid });
          if (scenario === "retry" && !state.failedOnce) {
            state.failedOnce = true;
            return HttpResponse.error();
          }
          return reply(op, 202, action);
        }
        if (op === "clarifyAction" || op === "cancelAction") {
          const rec = state.actions.get(String(params.action_id));
          if (!rec || rec.campaignId !== campaignId)
            return reply(op, 404, error("not_found"));
          if (body.expected_action_version !== rec.action.version)
            return reply(op, 409, error("stale_version"));
          if (op === "clarifyAction") {
            const answer = parseHttp("ClarifyAction", body);
            if (
              rec.action.status !== "needs_clarification" ||
              answer.clarification_id !== rec.action.clarification.id
            )
              return reply(op, 409, error("invalid_transition"));
            if (
              "choice_id" in answer.answer &&
              !rec.action.clarification.choices.some(
                (x) =>
                  x.id ===
                  ("choice_id" in answer.answer && answer.answer.choice_id),
              )
            )
              return reply(op, 400, error("invalid_request"));
            if (
              "text" in answer.answer &&
              !rec.action.clarification.allows_text
            )
              return reply(op, 400, error("invalid_request"));
            if (
              answer.expected_versions.scene !== snapshot.scene.version ||
              answer.expected_versions.character !== actor.version
            )
              return reply(op, 409, error("stale_version"));
            rec.clarified = true;
            rec.step = 0;
            rec.action = scenarioAction(
              scenario,
              rec.request,
              rec.action.id,
              "submitted",
            );
          } else {
            if (
              !["submitted", "needs_clarification"].includes(rec.action.status)
            )
              return reply(op, 409, error("invalid_transition"));
            rec.action = scenarioAction(
              scenario,
              rec.request,
              rec.action.id,
              "cancelled",
            );
          }
          state.bump(campaignId);
          state.receipts.set(id, { fingerprint, actionId: rec.action.id });
          return reply(op, op === "clarifyAction" ? 202 : 200, rec.action);
        }
        if (op === "createInvitation") {
          if (principal !== "gm") return reply(op, 403, error("forbidden"));
          const invite = parseHttp("InvitationRequest", body);
          if (
            invite.expected_membership_version !==
            snapshot.campaign.membership.version
          )
            return reply(op, 409, error("stale_version"));
          const value = {
            id: "invite-1",
            campaign_id: campaignId,
            token: "fixture-invitation-token".padEnd(40, "_"),
            role: invite.role,
            expires_at: "2026-09-07T22:00:00Z",
          };
          state.receipts.set(id, { fingerprint, value });
          return reply(op, 201, value);
        }
        if (op === "redeemInvitation") {
          if (body.token !== "fixture-invitation-token".padEnd(40, "_"))
            return reply(op, 404, error("not_found"));
          const value = snapshot.campaign.membership;
          state.receipts.set(id, { fingerprint, value });
          return reply(op, 200, value);
        }
        return reply(op, 404, error("not_found"));
      },
    ),
  );
  const liveUrl = `${origin.replace(/^http/, "ws")}/api/v1/live`;
  let endpoint = endpoints.get(liveUrl);
  if (!endpoint) {
    endpoint = ws.link(liveUrl);
    endpoints.set(liveUrl, endpoint);
  }
  const live = endpoint.addEventListener("connection", ({ client }) => {
    let principal: string | undefined;
    const active = new Map<
      string,
      { scope: Scope; epoch: string; cursor: string }
    >();
    const ended = new Map<
      string,
      Extract<ServerMessage, { type: "narration.ended" }>
    >();
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const send = (message: ServerMessage) => {
      parseServerMessage(message);
      client.send(JSON.stringify(message));
    };
    const later = (fn: () => void, ms = latency) => {
      const timer = setTimeout(() => {
        timers.delete(timer);
        fn();
      }, ms);
      timers.add(timer);
    };
    client.addEventListener("close", () => {
      timers.forEach(clearTimeout);
      timers.clear();
      active.clear();
    });
    client.addEventListener("message", (event) => {
      let message;
      try {
        message = parseClientMessage(JSON.parse(String(event.data)));
      } catch {
        client.close(1002, "invalid message");
        return;
      }
      if (message.type === "authenticate") {
        if (principal) {
          client.close(1002, "already authenticated");
          return;
        }
        principal = identify(`Bearer ${message.credential}`);
        if (!principal) {
          client.close(4401, "unauthenticated");
          return;
        }
        send({
          type: "authenticated",
          protocol_version: "1.0.0",
          request_id: message.request_id,
          connection_id: nextId(),
          principal_id: principal,
          expires_at: "2026-09-06T23:00:00Z",
        });
        return;
      }
      if (!principal) {
        client.close(4401, "unauthenticated");
        return;
      }
      const state = session(principal);
      if (message.type === "subscribe") {
        const { scope, subscription_id } = message;
        if (!campaigns.some((x) => x.id === scope.campaign_id)) {
          send({
            type: "error",
            protocol_version: "1.0.0",
            request_id: message.request_id,
            subscription_id,
            code: "not_found",
            retry_after_ms: null,
          });
          return;
        }
        const snapshot = state.snapshot(scope.campaign_id, scenario, principal);
        if (
          state.revoked ||
          scope.actor_id !== snapshot.characters[0]!.id ||
          scope.scene_id !== snapshot.scene.id
        ) {
          send({
            type: "error",
            protocol_version: "1.0.0",
            request_id: message.request_id,
            subscription_id,
            code: "not_found",
            retry_after_ms: null,
          });
          return;
        }
        if (active.has(subscription_id) || active.size >= 4) {
          client.close(1002, "subscription limit");
          return;
        }
        const epoch = `epoch_${principal}_${scope.scene_id}`;
        const base = {
          protocol_version: "1.0.0" as const,
          subscription_id,
          scope,
          visibility_epoch: epoch,
        };
        const currentCursor = cursor(
          state.revisions.get(scope.campaign_id) ?? 0,
        );
        const checkpoint = state.checkpoints.get(scope.campaign_id);
        const canReplay =
          message.resume &&
          checkpoint &&
          message.resume.cursor === checkpoint.before &&
          currentCursor === checkpoint.after;
        if (
          message.resume &&
          (message.resume.visibility_epoch !== epoch ||
            (message.resume.cursor !== currentCursor && !canReplay))
        ) {
          send({
            type: "stream.reset",
            protocol_version: "1.0.0",
            subscription_id,
            reason: "cursor_expired",
            retry_after_ms: 0,
          });
          return;
        }
        active.set(subscription_id, { scope, epoch, cursor: currentCursor });
        send({
          type: "subscribed",
          ...base,
          request_id: message.request_id,
          mode: message.resume ? "replay" : "snapshot",
        });
        const actions = [...state.actions.values()]
          .filter((x) => x.campaignId === scope.campaign_id)
          .map((x) => x.action);
        if (!message.resume) {
          const snapshot_id = nextId();
          const resources: Extract<
            ServerMessage,
            { type: "snapshot.resource" }
          >["resource"][] = [
            { kind: "campaign", value: snapshot.campaign },
            { kind: "scene", value: snapshot.scene },
            { kind: "character", value: snapshot.characters[0]! },
            { kind: "inventory", value: snapshot.inventories[0]! },
            ...actions.map((value) => ({ kind: "action" as const, value })),
          ];
          send({
            type: "snapshot.begin",
            ...base,
            snapshot_id,
            cursor: currentCursor,
            resource_count: resources.length,
          });
          resources.forEach((resource, index) =>
            send({
              type: "snapshot.resource",
              ...base,
              snapshot_id,
              index,
              resource,
            }),
          );
          send({
            type: "snapshot.end",
            ...base,
            snapshot_id,
            cursor: currentCursor,
            resource_count: resources.length,
          });
        }
        if (canReplay && checkpoint)
          send({
            type: "projection.invalidated",
            ...base,
            event_id: checkpoint.eventId,
            cursor: checkpoint.after,
            previous_cursor: checkpoint.before,
            occurred_at: stamp,
            correlation_id: requestId,
            causation_id: null,
            resources: [
              {
                resource_type: "scene",
                resource_id: scope.scene_id,
                version: snapshot.scene.version,
              },
              {
                resource_type: "character",
                resource_id: scope.actor_id,
                version: snapshot.characters[0]!.version,
              },
              {
                resource_type: "inventory",
                resource_id: scope.actor_id,
                version: snapshot.inventories[0]!.version,
              },
            ],
          });
        send({ type: "stream.ready", ...base, cursor: currentCursor });
        if (scenario === "revoked")
          later(() => {
            if (active.delete(subscription_id)) {
              state.revoked = true;
              send({
                type: "subscription.revoked",
                protocol_version: "1.0.0",
                subscription_id,
              });
            }
          });
        if (scenario === "reconnect" && !state.failedOnce) {
          state.failedOnce = true;
          later(() => {
            state.settled.add(scope.campaign_id);
            state.bump(scope.campaign_id);
            state.checkpoints.set(scope.campaign_id, {
              before: currentCursor,
              after: cursor(state.revisions.get(scope.campaign_id)!),
              eventId: nextId(),
            });
            client.close(1013, "fixture reconnect");
          });
        }
        if (scenario === "duplicate" || scenario === "out-of-order")
          later(() => {
            if (!active.has(subscription_id)) return;
            const change: ServerMessage = {
              type: "projection.invalidated",
              ...base,
              event_id: nextId(),
              cursor: cursor(1),
              previous_cursor: cursor(scenario === "out-of-order" ? 99 : 0),
              occurred_at: stamp,
              correlation_id: requestId,
              causation_id: null,
              resources: [
                {
                  resource_type: "scene",
                  resource_id: scope.scene_id,
                  version: "s2",
                },
              ],
            };
            active.get(subscription_id)!.cursor = change.cursor;
            send(change);
            if (scenario === "duplicate") send(change);
          });
        const action = [...actions]
          .reverse()
          .find((x) => x.status === "succeeded");
        if (action)
          later(() => {
            if (!active.has(subscription_id)) return;
            const narration_id = nextId();
            send({
              type: "narration.started",
              ...base,
              narration_id,
              action_id: action.id,
              command_id: action.command_id,
              basis: "committed",
              voice_session_id: null,
            });
            send({
              type: "narration.delta",
              ...base,
              narration_id,
              index: 0,
              text: "The linen catches the lamplight…",
            });
            later(() => {
              if (!active.has(subscription_id) || ended.has(narration_id))
                return;
              if (scenario !== "narration-failure")
                send({
                  type: "narration.delta",
                  ...base,
                  narration_id,
                  index: 1,
                  text: " You draw the clean linen tight. Footsteps fade into the rain.",
                });
              const end: Extract<ServerMessage, { type: "narration.ended" }> = {
                type: "narration.ended",
                ...base,
                narration_id,
                status:
                  scenario === "narration-failure" ? "failed" : "completed",
                chunk_count: scenario === "narration-failure" ? 1 : 2,
                request_id: null,
              };
              ended.set(narration_id, end);
              send(end);
            }, latency * 3);
            ended.set(`active:${narration_id}`, {
              type: "narration.ended",
              ...base,
              narration_id,
              status: "interrupted",
              chunk_count: 1,
              request_id: null,
            });
          });
      } else if (message.type === "unsubscribe") {
        active.delete(message.subscription_id);
        send({
          type: "unsubscribed",
          protocol_version: "1.0.0",
          request_id: message.request_id,
          subscription_id: message.subscription_id,
        });
      } else if (message.type === "narration.interrupt") {
        const end =
          ended.get(message.narration_id) ??
          ended.get(`active:${message.narration_id}`);
        if (
          !end ||
          end.subscription_id !== message.subscription_id ||
          !active.has(message.subscription_id)
        ) {
          send({
            type: "error",
            protocol_version: "1.0.0",
            request_id: message.request_id,
            subscription_id: message.subscription_id,
            code: "not_found",
            retry_after_ms: null,
          });
          return;
        }
        const response = { ...end, request_id: message.request_id };
        ended.set(message.narration_id, response);
        send(response);
      } else if (message.type === "ack") {
        if (message.cursor !== active.get(message.subscription_id)?.cursor)
          client.close(1002, "invalid cursor acknowledgement");
      }
    });
  });
  return {
    handlers: [...handlers, live],
    sessions,
    reset: () => sessions.clear(),
  };
}
