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
/** One phrasing for a detail the current connection does not project. */
export const notSupplied = (subject: string) =>
  `${subject} are not supplied by this connection.`;
/** The reason a campaign that does not advertise an action kind cannot run it. */
export function capabilityReason(kind: string): string {
  return (
    {
      text: providerReason.text,
      question: providerReason.question,
      inspect: "This adventure defines no examination for these details.",
      move: "This adventure defines no travel from this scene.",
      use_item: "This adventure defines no usable items.",
      wait: "This adventure does not let time be passed on request.",
    }[kind] ?? "This campaign does not support that action."
  );
}
