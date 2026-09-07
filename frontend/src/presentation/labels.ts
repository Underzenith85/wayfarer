/**
 * The single presentation boundary between engine identifiers and player text.
 *
 * Engine keys (`attribute:st`), lifecycle enum values (`pause`) and load bands
 * (`extra-heavy`) are addressing, not prose. Every player-facing surface routes
 * them through this module so no raw identifier reaches a reader, and so
 * attributes keep their canonical GURPS order instead of sorting alphabetically.
 */
export interface StatLabel {
  /** Compact label for dense grids, e.g. "ST". */
  short: string;
  /** Full name for tooltips and accessible names, e.g. "Strength". */
  full: string;
}
export interface PresentedStat extends StatLabel {
  id: string;
  value: number;
}
const NAMES: Record<string, StatLabel> = {
  st: { short: "ST", full: "Strength" },
  dx: { short: "DX", full: "Dexterity" },
  iq: { short: "IQ", full: "Intelligence" },
  ht: { short: "HT", full: "Health" },
  hp: { short: "HP", full: "Hit points" },
  fp: { short: "FP", full: "Fatigue points" },
  will: { short: "Will", full: "Will" },
  per: { short: "Per", full: "Perception" },
  "basic-speed": { short: "Speed", full: "Basic Speed" },
  speed: { short: "Speed", full: "Basic Speed" },
  "basic-move": { short: "Move", full: "Basic Move" },
  move: { short: "Move", full: "Move" },
  "basic-lift": { short: "Lift", full: "Basic Lift" },
  dodge: { short: "Dodge", full: "Dodge" },
  parry: { short: "Parry", full: "Parry" },
  block: { short: "Block", full: "Block" },
};
/** Canonical reading order; ST/DX/IQ/HT first, then secondaries and defenses. */
const ORDER = [
  "st",
  "dx",
  "iq",
  "ht",
  "hp",
  "will",
  "per",
  "fp",
  "basic-speed",
  "speed",
  "basic-move",
  "move",
  "basic-lift",
  "dodge",
  "parry",
  "block",
];
const NAMESPACE = /^[a-z][a-z0-9_-]*:/i;
/** Drops the engine namespace so `attribute:st` and `st` resolve identically. */
export function engineKey(id: string): string {
  return id.replace(NAMESPACE, "").toLowerCase();
}
/** Readable fallback for identifiers this mapping does not name explicitly. */
export function humanize(id: string): string {
  const words = engineKey(id)
    .split(/[-_\s]+/)
    .filter(Boolean);
  if (!words.length) return id;
  return words.map((word) => word[0]!.toUpperCase() + word.slice(1)).join(" ");
}
/**
 * Resolves display text for one statistic. A label the service already wrote in
 * prose wins over the fallback; a label echoing the engine key never does.
 */
export function statLabel(stat: { id: string; label?: string }): StatLabel {
  const key = engineKey(stat.id);
  const named = NAMES[key];
  if (named) return named;
  const label = stat.label?.trim();
  const authored =
    label && label !== stat.id && label !== key ? label : humanize(stat.id);
  return { short: authored, full: authored };
}
/** Canonical order first, then anything unrecognised in the order received. */
export function orderStats<T>(
  items: readonly T[],
  id: (item: T) => string,
): T[] {
  return items
    .map((item, index) => {
      const rank = ORDER.indexOf(engineKey(id(item)));
      return { item, rank: rank === -1 ? ORDER.length : rank, index };
    })
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.item);
}
export function presentStats(
  stats: readonly { id: string; label?: string; value: number }[],
): PresentedStat[] {
  return orderStats(stats, (stat) => stat.id).map((stat) => ({
    id: stat.id,
    value: stat.value,
    ...statLabel(stat),
  }));
}
/**
 * Full name for a catalog definition a player buys or edits: an attribute,
 * secondary characteristic, skill, trait or piece of equipment.
 */
export function definitionLabel(id: string): string {
  return statLabel({ id }).full;
}
/** Host controls over the campaign lifecycle, phrased as the action taken. */
export const lifecycleLabel: Record<string, string> = {
  activate: "Start game",
  pause: "Pause session",
  resume: "Resume session",
  complete: "End campaign",
  archive: "Archive campaign",
  unarchive: "Restore from archive",
  continue: "Continue to next adventure",
};
export function lifecycleOperationLabel(operation: string): string {
  return lifecycleLabel[operation] ?? humanize(operation);
}
const PHASES: Record<string, string> = {
  draft: "Draft",
  ready: "Ready to start",
  active: "In play",
  paused: "Paused",
  completed: "Finished",
  archived: "Archived",
};
export function campaignPhaseLabel(phase: string): string {
  return PHASES[phase] ?? humanize(phase);
}
const CONDITIONS: Record<string, { label: string; description: string }> = {
  unconscious: {
    label: "Unconscious",
    description: "Cannot act or defend until revived.",
  },
  stunned: {
    label: "Stunned",
    description: "Reeling; recovery is checked before acting normally.",
  },
  restrained: {
    label: "Restrained",
    description: "Held or bound; movement is prevented until freed.",
  },
};
/**
 * Conditions arrive as enum values, sometimes with the value repeated as label
 * and description. Named conditions get prose; the rest get a readable name.
 */
export function conditionLabel(condition: {
  id: string;
  label?: string;
  description?: string;
}): { label: string; description: string } {
  const known = CONDITIONS[engineKey(condition.id)];
  const raw = (value: string | undefined) =>
    !value || value === condition.id || value === engineKey(condition.id);
  return {
    label:
      known?.label ??
      (raw(condition.label) ? humanize(condition.id) : condition.label!),
    description: raw(condition.description)
      ? (known?.description ?? "")
      : condition.description!,
  };
}
const ENCUMBRANCE: Record<string, string> = {
  none: "None",
  light: "Light",
  medium: "Medium",
  heavy: "Heavy",
  "extra-heavy": "Extra-heavy",
  extra_heavy: "Extra-heavy",
};
/**
 * Load band, or null when the projection reported no band. Callers say
 * "not reported" rather than printing whatever placeholder arrived.
 */
export function encumbranceLabel(
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  return ENCUMBRANCE[value.trim().toLowerCase()] ?? null;
}
