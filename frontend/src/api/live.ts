import type { ClientMessage, ServerMessage, Scope } from "./events.generated";
import { parseClientMessage, parseServerMessage } from "./validation";
import { TransportError } from "../play/transport";
export interface LiveOptions {
  url: string;
  credential: string;
  scope: Scope;
  signal: AbortSignal;
  principalId?: string;
  resume?: { cursor: string; visibility_epoch: string } | null;
  socketFactory?: (url: string, protocol: string) => WebSocket;
}
/** Typed wire seam shared by real WebSockets and MSW. Reconnect policy belongs to #54. */
export async function* liveMessages(
  options: LiveOptions,
): AsyncGenerator<ServerMessage> {
  const socket = (
    options.socketFactory ?? ((url, protocol) => new WebSocket(url, protocol))
  )(options.url, "wayfarer.live.v1");
  const subscription_id = crypto.randomUUID();
  const queue: { message: ServerMessage; bytes: number }[] = [];
  let queuedBytes = 0;
  let failure: unknown;
  let closed = false;
  let wake: () => void = () => {};
  const send = (value: ClientMessage) =>
    socket.send(JSON.stringify(parseClientMessage(value)));
  const fail = (error: unknown) => {
    failure = error;
    wake();
  };
  const abort = () => {
    closed = true;
    fail(options.signal.reason ?? new DOMException("Aborted", "AbortError"));
    socket.close(1000);
  };
  const timeout = setTimeout(
    () => fail(new TransportError("network", "Live connection timed out.")),
    10000,
  );
  socket.addEventListener("open", () =>
    send({
      type: "authenticate",
      request_id: crypto.randomUUID(),
      credential: options.credential,
    }),
  );
  socket.addEventListener("message", (event) => {
    try {
      if (
        typeof event.data !== "string" ||
        new TextEncoder().encode(event.data).length > 8388608
      )
        throw new Error("Oversized or non-text live frame");
      const message = parseServerMessage(JSON.parse(event.data));
      if (message.type === "authenticated") {
        if (options.principalId && message.principal_id !== options.principalId)
          throw new TransportError(
            "unauthenticated",
            "Live principal mismatch.",
          );
        clearTimeout(timeout);
        send({
          type: "subscribe",
          request_id: crypto.randomUUID(),
          subscription_id,
          scope: options.scope,
          resume: options.resume ?? null,
        });
        return;
      }
      if (message.type === "heartbeat.ping") {
        send({
          type: "heartbeat.pong",
          request_id: crypto.randomUUID(),
          nonce: message.nonce,
        });
        return;
      }
      if (
        "subscription_id" in message &&
        message.subscription_id !== null &&
        message.subscription_id !== subscription_id
      )
        return;
      if (
        "scope" in message &&
        (message.scope.campaign_id !== options.scope.campaign_id ||
          message.scope.scene_id !== options.scope.scene_id ||
          message.scope.actor_id !== options.scope.actor_id)
      )
        throw new Error("Live scope mismatch");
      if (
        queue.length >= 256 ||
        (queuedBytes += new TextEncoder().encode(event.data).length) > 16777216
      )
        throw new Error("Live buffer limit exceeded");
      queue.push({
        message,
        bytes: new TextEncoder().encode(event.data).length,
      });
      wake();
    } catch (error) {
      fail(error);
    }
  });
  socket.addEventListener("error", () =>
    fail(new TransportError("network", "Live connection failed.")),
  );
  socket.addEventListener("close", (event) => {
    closed = true;
    if (event.code !== 1000)
      failure = new TransportError(
        event.code === 4401 ? "unauthenticated" : "network",
        `Live connection closed (${event.code}).`,
      );
    wake();
  });
  options.signal.addEventListener("abort", abort, { once: true });
  if (options.signal.aborted) abort();
  try {
    while (true) {
      if (failure) throw failure;
      const next = queue.shift();
      if (next) {
        queuedBytes -= next.bytes;
        yield next.message;
        continue;
      }
      if (closed) return;
      await new Promise<void>((resolve) => {
        wake = resolve;
      });
    }
  } finally {
    clearTimeout(timeout);
    options.signal.removeEventListener("abort", abort);
    if (socket.readyState === WebSocket.OPEN)
      send({
        type: "unsubscribe",
        request_id: crypto.randomUUID(),
        subscription_id,
      });
    socket.close(1000);
    queue.length = 0;
  }
}
