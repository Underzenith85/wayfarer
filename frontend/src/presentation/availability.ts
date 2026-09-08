/**
 * One wording for every "not connected yet" condition. A condition is stated in
 * exactly one place — the shell banner — and every control blocked by it repeats
 * only the part that applies to that control.
 */
export const providerBanner = {
  /** The single sentence naming the condition, shown once per shell. */
  summary: "This game has no AI provider connected.",
  disclosure: "What is affected?",
  /** The capabilities the missing provider actually removes. */
  affected: [
    "Free-text actions and character dialogue in play.",
    "Out-of-character questions to the game master.",
    "Story narration for results the engine has already committed.",
    "AI scenario and character creation in setup.",
  ],
  /** What still works, so the banner is not read as “the game is down”. */
  retained:
    "Authored adventures, scene actions, saved scenarios, manual editing, import and export are unaffected.",
} as const;
/** Reasons stated on the controls the missing provider blocks. */
export const providerReason = {
  text: "Free-text actions need an AI provider. Use the scene actions above for now.",
  question:
    "Out-of-character questions need an AI provider. Use the scene actions above for now.",
  creation: "AI creation needs an AI provider.",
} as const;
/**
 * The reason a campaign that does not advertise an action kind cannot run it.
 * These lines sit in the scene card, above the transcript, so they are written
 * as something a player is told about the place they are standing in — never as
 * a statement about what the adventure's data does or does not define (#272).
 */
export function capabilityReason(kind: string): string {
  return (
    {
      text: providerReason.text,
      question: providerReason.question,
      inspect: "There is nothing more to make out from here.",
      move: "There is nowhere to travel to from here.",
      use_item: "Nothing you are carrying can be used here.",
      wait: "Time does not pass here on request.",
    }[kind] ?? "That is not something you can do here."
  );
}
