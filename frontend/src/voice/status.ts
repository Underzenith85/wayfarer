import type { VoiceState } from "./controller";

/** What the voice pipeline is doing, in one sentence for the composer's live region. */
export function voiceStatus(state: VoiceState) {
  return state.capture === "requesting"
    ? "Requesting microphone permission…"
    : state.capture === "listening"
      ? "Listening — release to review"
      : state.capture === "interpreting"
        ? "Interpreting speech…"
        : state.capture === "review"
          ? "Review transcript — nothing has been sent"
          : state.speaking
            ? "Narrating — action already committed"
            : state.stage === "interpreting"
              ? "Interpreting action…"
              : state.stage === "resolving"
                ? "Resolving action…"
                : state.stage === "narrating"
                  ? "Preparing narration — action already committed"
                  : "";
}
