import {
  disconnectedTransport,
  TransportError,
  type PlayTransport,
  type Snapshot,
} from "../play/transport";
import type {
  Command,
  Identity,
  OnboardingPort,
  OnboardingView,
} from "./model";
export function onboardingTransport(
  identity: Identity,
  room: string,
): PlayTransport {
  async function request<T>(body: unknown, signal: AbortSignal): Promise<T> {
    const response = await fetch("/__fixtures/onboarding", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-mock-identity": identity,
        "x-mock-room": room,
      },
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    });
    if (!response.ok) {
      const e = (await response.json()) as {
        code: TransportError["code"];
        message: string;
      };
      throw new TransportError(e.code, e.message);
    }
    return response.json() as Promise<T>;
  }
  const onboarding: OnboardingPort = {
    read: (signal) => request<OnboardingView>({ op: "read" }, signal),
    command: (command: Command, signal) =>
      request<OnboardingView>({ op: "command", command }, signal),
  };
  return {
    ...disconnectedTransport,
    principalId: `onboarding:${room}:${identity}`,
    sample: true,
    onboarding,
    async listCampaigns(signal) {
      const view = await onboarding.read(signal);
      return view.lobby?.status === "active"
        ? [(await request<Snapshot>({ op: "snapshot" }, signal)).campaign]
        : [];
    },
    readSnapshot: (id, signal) =>
      id === "campaign-1"
        ? request<Snapshot>({ op: "snapshot" }, signal)
        : Promise.reject(
            new TransportError("not_found", "Campaign unavailable."),
          ),
  };
}
