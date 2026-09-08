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
/**
 * Reads a runtime pool identifier (`hp:mira`) as the character it belongs to and
 * the pool's name, so a recovery list is prose rather than engine addressing.
 */
export function poolLabel(
  id: string,
  actorName: (actorId: string) => string,
): string {
  const separator = id.indexOf(":");
  if (separator === -1) return statLabel({ id }).short;
  const kind = statLabel({ id: id.slice(0, separator) }).short;
  return `${actorName(id.slice(separator + 1))} · ${kind}`;
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
/**
 * Difficulty is a wire value (`gentle`); every surface a player reads shows the
 * same title-cased name, so the concept step, the AI workshop and the review
 * never disagree about what was chosen (#264).
 */
const DIFFICULTIES: Record<string, string> = {
  gentle: "Gentle",
  standard: "Standard",
  hard: "Hard",
};
export const difficulties = ["gentle", "standard", "hard"] as const;
export function difficultyLabel(value: string): string {
  return DIFFICULTIES[value.trim().toLowerCase()] ?? humanize(value);
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
/**
 * Why a campaign that is not in play accepts no turn. Reaching the end of an
 * adventure and being locked out of one are different conditions, and a single
 * authorization sentence for both told a player who had just won that they were
 * not allowed to act (#297).
 */
const LIFECYCLE_REASONS: Record<string, string> = {
  draft: "This campaign has not started yet, so it accepts no actions.",
  ready: "This campaign has not started yet, so it accepts no actions.",
  paused: "This campaign is paused, so it accepts no actions.",
  completed: "This adventure is finished. Its story accepts no further turns.",
  archived: "This campaign is archived, so it accepts no actions.",
};
export function lifecycleReason(status: string): string {
  return (
    LIFECYCLE_REASONS[status] ??
    "This campaign is not active, so it accepts no actions."
  );
}
/**
 * Engine bookkeeping named for a reader: what a committed turn changed, without
 * the identifier or the content digest that names it to the service (#296).
 */
const RESOURCES: Record<string, string> = {
  campaign: "the campaign",
  character: "your character",
  inventory: "your inventory",
  scene: "this scene",
  session: "the session",
};
export function resourceLabel(resourceType: string): string {
  return RESOURCES[engineKey(resourceType)] ?? humanize(resourceType);
}
/** "your character and this scene" — a readable list of what a turn touched. */
export function changedLabel(
  resources: readonly { resource_type: string }[],
): string {
  const names = [
    ...new Set(resources.map((r) => resourceLabel(r.resource_type))),
  ];
  if (names.length < 2) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]!}`;
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
/**
 * The scene description, or null when the projection simply echoed the title.
 * The v1 scene contract requires a non-empty description, and the authored
 * scene graph carries no prose for one, so the projection repeats the location
 * name. A card prints the name once and leaves the slot empty rather than
 * twice (#203).
 */
export function sceneDescription(
  title: string,
  description: string | null | undefined,
): string | null {
  const value = description?.trim();
  if (!value || value.toLowerCase() === title.trim().toLowerCase()) return null;
  return value;
}
/** Time of day for an entry made today; the date as well for an older one. */
export function timestampLabel(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time: Intl.DateTimeFormatOptions = {
    hour: "numeric",
    minute: "2-digit",
  };
  return at.toDateString() === now.toDateString()
    ? at.toLocaleTimeString(undefined, time)
    : at.toLocaleString(undefined, { month: "short", day: "numeric", ...time });
}
const MINUTE = 60000,
  HOUR = 60 * MINUTE,
  DAY = 24 * HOUR;
/**
 * How long ago something was saved, in the words a player would use. A draft
 * restored from an earlier session says its age so it is never mistaken for
 * something typed a moment ago (#201).
 */
export function ageLabel(iso: string, now: number = Date.now()): string {
  const at = new Date(iso).getTime();
  if (Number.isNaN(at)) return "";
  const elapsed = Math.max(0, now - at);
  const count = (unit: number) => Math.floor(elapsed / unit);
  const plural = (value: number, unit: string) =>
    `${value} ${unit}${value === 1 ? "" : "s"} ago`;
  if (elapsed < MINUTE) return "just now";
  if (elapsed < HOUR) return plural(count(MINUTE), "minute");
  if (elapsed < DAY) return plural(count(HOUR), "hour");
  return plural(count(DAY), "day");
}
