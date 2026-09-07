import { useEffect, useId, useState } from "react";
import { Button } from "../components/ui/button";
import {
  definitionLabel,
  engineKey,
  orderStats,
  statLabel,
} from "../presentation/labels";
import type { components } from "./workshop.generated";
export type Proposal = Omit<
  components["schemas"]["CharacterProposal"],
  "draft"
> & {
  draft: Omit<components["schemas"]["CharacterDraft"], "purchases"> & {
    purchases: (Omit<components["schemas"]["Purchase"], "trait"> & {
      trait?: components["schemas"]["TraitOptions"] | null;
    })[];
  };
};
export type CharacterPreview = components["schemas"]["CharacterPreviewResult"];
type TraitOptions = components["schemas"]["TraitOptions"];
export type PreviewCharacter = (
  proposal: Proposal,
  signal: AbortSignal,
) => Promise<CharacterPreview>;
const groups = [
  "Attributes",
  "Advantages",
  "Disadvantages",
  "Skills",
  "Equipment",
] as const;
type Group = (typeof groups)[number];
type Draft = Proposal["draft"];
type CatalogOption = CharacterPreview["catalog"][number];
/**
 * GURPS primary attributes are fixed: every character has exactly ST, DX, IQ
 * and HT. They are rendered as labelled steppers, never as entries of a list a
 * player could remove, duplicate, or replace with a skill (#266).
 */
const PRIMARY_ATTRIBUTES = [
  "attribute:st",
  "attribute:dx",
  "attribute:iq",
  "attribute:ht",
] as const;
/** The GURPS average, and what an attribute nobody has paid for is worth. */
const ATTRIBUTE_DEFAULT = 10;
/** What the add and remove controls of a genuinely variable section call one entry. */
const entryNoun: Record<Group, string> = {
  Attributes: "secondary characteristic",
  Advantages: "advantage",
  Disadvantages: "disadvantage",
  Skills: "skill",
  Equipment: "equipment",
};
const sentenceCase = (value: string) =>
  value[0]!.toUpperCase() + value.slice(1);
/**
 * Every attribute the server requires of a build: the four primaries, plus any
 * further attribute the pinned profile's catalog carries.
 */
function primaryAttributes(catalog: readonly CatalogOption[]): string[] {
  const ids = new Set<string>(PRIMARY_ATTRIBUTES);
  for (const definition of catalog)
    if (definition.kind === "attribute") ids.add(definition.id);
  return orderStats([...ids], (id) => id);
}
/**
 * The same draft with exactly one purchase per primary attribute, so an edit,
 * a template or a generated draft can neither drop one nor buy one twice.
 */
function withPrimaryAttributes(
  draft: Draft,
  attributes: readonly string[],
): Draft {
  const purchases = draft.purchases ?? [];
  const seen = new Set<string>();
  const kept = purchases.filter((purchase) => {
    if (!attributes.includes(purchase.definition_id)) return true;
    if (seen.has(purchase.definition_id)) return false;
    seen.add(purchase.definition_id);
    return true;
  });
  const missing = attributes
    .filter((id) => !seen.has(id))
    .map((id) => ({ definition_id: id, amount: ATTRIBUTE_DEFAULT }));
  if (!missing.length && kept.length === purchases.length) return draft;
  return { ...draft, purchases: [...kept, ...missing] };
}

export function CharacterDraftEditor({
  proposal,
  onChange,
  preview,
  disabled = false,
  templates = [],
}: {
  proposal: Proposal;
  onChange: (proposal: Proposal) => void;
  preview: PreviewCharacter;
  disabled?: boolean;
  templates?: { title: string; proposal: Proposal }[];
}) {
  const id = useId();
  const [feedback, setFeedback] = useState<{
    key: string;
    source: PreviewCharacter;
    result: CharacterPreview;
  } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(
    null,
  );
  const key = JSON.stringify(proposal);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setError(null);
      void preview(JSON.parse(key) as Proposal, controller.signal)
        .then((result) => {
          if (!controller.signal.aborted)
            setFeedback({ key, source: preview, result });
        })
        .catch((e: unknown) => {
          if (!controller.signal.aborted)
            setError({
              key,
              message: e instanceof Error ? e.message : "Preview unavailable",
            });
        });
    }, 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, preview]);
  const current =
    feedback?.key === key && feedback.source === preview
      ? feedback.result
      : null;
  const catalog = feedback?.result.catalog ?? [];
  const attributes = primaryAttributes(catalog);
  const attributeKey = attributes.join(" ");
  const change = (draft: Draft) => onChange({ ...proposal, draft });
  // A draft that reaches the editor short of an attribute, or carrying one
  // twice, is repaired before the player can act on it; a read-only editor
  // shows what was saved and changes nothing.
  useEffect(() => {
    if (disabled) return;
    const draft = withPrimaryAttributes(
      proposal.draft,
      attributeKey.split(" "),
    );
    if (draft !== proposal.draft) onChange({ ...proposal, draft });
  }, [attributeKey, disabled, proposal, onChange]);
  const setAttribute = (definitionId: string, amount: number) => {
    const purchases = proposal.draft.purchases ?? [];
    change({
      ...proposal.draft,
      purchases: purchases.some((p) => p.definition_id === definitionId)
        ? purchases.map((p) =>
            p.definition_id === definitionId ? { ...p, amount } : p,
          )
        : [...purchases, { definition_id: definitionId, amount }],
    });
  };
  const category = (definitionId: string) => {
    const d = catalog.find((entry) => entry.id === definitionId);
    if (
      d?.kind === "attribute" ||
      d?.kind === "secondary" ||
      definitionId.startsWith("attribute:") ||
      definitionId.startsWith("secondary:")
    )
      return "Attributes";
    if (d?.kind === "skill" || definitionId.startsWith("skill:"))
      return "Skills";
    if (d?.kind === "equipment") return "Equipment";
    return (d?.point_cost ?? 0) < 0 ? "Disadvantages" : "Advantages";
  };
  return (
    <div className="character-draft-editor">
      <div
        className="point-budget"
        aria-live="polite"
        aria-label="Point budget"
        data-overspent={current !== null && current.remaining < 0}
      >
        {current ? (
          <>
            <strong>
              {current.spent} / {current.spent + current.remaining} points
            </strong>
            <span>
              {current.remaining < 0
                ? `${-current.remaining} points over budget`
                : `${current.remaining} points remaining`}
            </span>
          </>
        ) : (
          <strong>
            {error?.key === key
              ? "Point preview unavailable"
              : "Calculating points…"}
          </strong>
        )}
      </div>
      {error?.key === key && <p role="alert">{error.message}</p>}
      {current?.diagnostics.map((message, i) => (
        <p role="alert" key={i}>
          {message}
        </p>
      ))}
      <fieldset disabled={disabled} className="draft-controls">
        <details open>
          <summary>Concept</summary>
          {templates.length > 0 && (
            <label>
              Start from a character template
              <select
                value=""
                onChange={(e) => {
                  const selected = templates[Number(e.target.value)];
                  if (selected) onChange(structuredClone(selected.proposal));
                }}
              >
                <option value="">Choose a starting character</option>
                {templates.map((template, i) => (
                  <option key={i} value={i}>
                    {template.title}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label htmlFor={`${id}-name`}>Name</label>
          <input
            id={`${id}-name`}
            value={proposal.draft.name}
            maxLength={200}
            onChange={(e) =>
              change({ ...proposal.draft, name: e.target.value })
            }
          />
          <label htmlFor={`${id}-backstory`}>Concept and backstory</label>
          <textarea
            id={`${id}-backstory`}
            value={proposal.draft.backstory ?? ""}
            maxLength={10000}
            onChange={(e) =>
              change({ ...proposal.draft, backstory: e.target.value })
            }
          />
        </details>
        {groups.map((group) => (
          <details
            key={group}
            open={group === "Attributes" || group === "Skills"}
          >
            <summary>
              {group}
              {current?.breakdown.length
                ? ` · ${current.breakdown.filter((entry) => category(entry.definition_id) === group).reduce((total, entry) => total + entry.cost, 0)} pts`
                : ""}
            </summary>
            {group === "Attributes" &&
              attributes.map((definitionId) => {
                const label = definitionLabel(definitionId);
                const field = `${id}-${engineKey(definitionId)}`;
                const amount =
                  proposal.draft.purchases?.find(
                    (p) => p.definition_id === definitionId,
                  )?.amount ?? ATTRIBUTE_DEFAULT;
                return (
                  <div
                    key={definitionId}
                    className="purchase-row attribute-row"
                  >
                    <label htmlFor={field}>{label}</label>
                    <div className="purchase-stepper">
                      <Button
                        type="button"
                        aria-label={`Decrease ${label}`}
                        disabled={amount <= 1}
                        onClick={() => setAttribute(definitionId, amount - 1)}
                      >
                        −
                      </Button>
                      <input
                        id={field}
                        type="number"
                        min={1}
                        max={10000}
                        value={amount}
                        onChange={(e) =>
                          setAttribute(definitionId, Number(e.target.value))
                        }
                      />
                      <Button
                        type="button"
                        aria-label={`Increase ${label}`}
                        disabled={amount >= 10000}
                        onClick={() => setAttribute(definitionId, amount + 1)}
                      >
                        +
                      </Button>
                    </div>
                    <span className="purchase-cost">
                      {current?.breakdown.find(
                        (entry) => entry.definition_id === definitionId,
                      )?.cost ?? "—"}{" "}
                      pts
                    </span>
                  </div>
                );
              })}
            {proposal.draft.purchases.map((p, i) => {
              const variable =
                category(p.definition_id) === group &&
                !attributes.includes(p.definition_id);
              if (!variable) return null;
              const entry = `${entryNoun[group]} ${
                proposal.draft.purchases.filter(
                  (v, j) =>
                    j < i &&
                    category(v.definition_id) === group &&
                    !attributes.includes(v.definition_id),
                ).length + 1
              }`;
              return (
                <div key={i} className="context-actions purchase-row">
                  <label htmlFor={`${id}-purchase-${i}`}>
                    {sentenceCase(entry)}
                  </label>
                  <select
                    id={`${id}-purchase-${i}`}
                    value={p.definition_id}
                    onChange={(e) =>
                      change({
                        ...proposal.draft,
                        purchases: proposal.draft.purchases.map((v, j) =>
                          j === i
                            ? { definition_id: e.target.value, amount: 1 }
                            : v,
                        ),
                      })
                    }
                  >
                    {catalog
                      .filter(
                        (d) =>
                          (category(d.id) === group &&
                            !attributes.includes(d.id)) ||
                          d.id === p.definition_id,
                      )
                      .map((d) => (
                        <option
                          key={d.id}
                          value={d.id}
                          disabled={d.status !== "implemented"}
                        >
                          {d.name}
                          {d.status !== "implemented" ? " · Unavailable" : ""}
                        </option>
                      ))}
                  </select>
                  <label htmlFor={`${id}-amount-${i}`}>
                    {definitionLabel(p.definition_id)}
                  </label>
                  <div className="purchase-stepper">
                    <Button
                      type="button"
                      aria-label={`Decrease ${definitionLabel(p.definition_id)}`}
                      disabled={p.amount <= 1}
                      onClick={() =>
                        change({
                          ...proposal.draft,
                          purchases: proposal.draft.purchases.map((v, j) =>
                            j === i ? { ...v, amount: (v.amount ?? 1) - 1 } : v,
                          ),
                        })
                      }
                    >
                      −
                    </Button>
                    <input
                      id={`${id}-amount-${i}`}
                      type="number"
                      min={1}
                      max={10000}
                      value={p.amount}
                      onChange={(e) =>
                        change({
                          ...proposal.draft,
                          purchases: proposal.draft.purchases.map((v, j) =>
                            j === i
                              ? { ...v, amount: Number(e.target.value) }
                              : v,
                          ),
                        })
                      }
                    />
                    <Button
                      type="button"
                      aria-label={`Increase ${definitionLabel(p.definition_id)}`}
                      disabled={p.amount >= 10000}
                      onClick={() =>
                        change({
                          ...proposal.draft,
                          purchases: proposal.draft.purchases.map((v, j) =>
                            j === i ? { ...v, amount: (v.amount ?? 1) + 1 } : v,
                          ),
                        })
                      }
                    >
                      +
                    </Button>
                  </div>
                  <span className="purchase-cost">
                    {current?.breakdown.find(
                      (entry) => entry.definition_id === p.definition_id,
                    )?.cost ?? "—"}{" "}
                    pts
                  </span>
                  <Button
                    type="button"
                    onClick={() =>
                      change({
                        ...proposal.draft,
                        purchases: proposal.draft.purchases.filter(
                          (_, j) => j !== i,
                        ),
                      })
                    }
                  >
                    Remove {entry}
                  </Button>
                  {(() => {
                    const definition = catalog.find(
                      (d) => d.id === p.definition_id,
                    );
                    const updateTrait = (trait: TraitOptions) =>
                      change({
                        ...proposal.draft,
                        purchases: proposal.draft.purchases.map((value, j) =>
                          j === i ? { ...value, trait } : value,
                        ),
                      });
                    const trait = p.trait ?? {
                      parameters: [],
                      modifiers: [],
                      self_control: null,
                    };
                    return (
                      <>
                        {definition?.skill && (
                          <p>
                            {
                              statLabel({ id: definition.skill.attribute })
                                .short
                            }{" "}
                            / {definition.skill.difficulty}
                            {definition.skill.specialty &&
                              ` · ${definition.skill.specialty.name}`}
                            {definition.skill.technique &&
                              ` · Technique: ${definitionLabel(definition.skill.technique.parent)}, cap +${definition.skill.technique.maximum_modifier}`}
                            {definition.skill.defaults
                              .map(
                                (d) =>
                                  ` · Default: ${definitionLabel(d.target)} ${d.modifier}`,
                              )
                              .join("")}
                          </p>
                        )}
                        {definition?.trait?.self_control && (
                          <label>
                            Self-control
                            <select
                              aria-label={`Self-control ${i + 1}`}
                              value={trait.self_control ?? ""}
                              onChange={(e) =>
                                updateTrait({
                                  ...trait,
                                  self_control: Number(e.target.value) as
                                    6 | 9 | 12 | 15,
                                })
                              }
                            >
                              <option value="" disabled>
                                Select rating
                              </option>
                              {[6, 9, 12, 15].map((value) => (
                                <option key={value}>{value}</option>
                              ))}
                            </select>
                          </label>
                        )}
                        {definition?.trait?.parameters.map((parameter) => (
                          <label key={parameter.name}>
                            {parameter.name}
                            <select
                              aria-label={`${parameter.name} ${i + 1}`}
                              value={
                                JSON.stringify(
                                  trait.parameters.find(
                                    (v) => v[0] === parameter.name,
                                  )?.[1],
                                ) ?? ""
                              }
                              onChange={(e) =>
                                updateTrait({
                                  ...trait,
                                  parameters: [
                                    ...trait.parameters.filter(
                                      (v) => v[0] !== parameter.name,
                                    ),
                                    [
                                      parameter.name,
                                      JSON.parse(e.target.value) as
                                        string | number | boolean,
                                    ],
                                  ],
                                })
                              }
                            >
                              <option value="" disabled>
                                Select value
                              </option>
                              {parameter.choices.map((value) => (
                                <option
                                  key={JSON.stringify(value)}
                                  value={JSON.stringify(value)}
                                >
                                  {String(value)}
                                </option>
                              ))}
                            </select>
                          </label>
                        ))}
                        {definition?.trait?.modifiers.map((modifier) => (
                          <label key={modifier.id}>
                            <input
                              type="checkbox"
                              checked={trait.modifiers.includes(modifier.id)}
                              onChange={(e) =>
                                updateTrait({
                                  ...trait,
                                  modifiers: e.target.checked
                                    ? [...trait.modifiers, modifier.id]
                                    : trait.modifiers.filter(
                                        (id) => id !== modifier.id,
                                      ),
                                })
                              }
                            />
                            {modifier.id} ({modifier.percent}%)
                          </label>
                        ))}
                      </>
                    );
                  })()}
                </div>
              );
            })}

            <Button
              type="button"
              disabled={
                !catalog.some(
                  (d) =>
                    category(d.id) === group &&
                    !attributes.includes(d.id) &&
                    d.status === "implemented",
                )
              }
              onClick={() => {
                const definition = catalog.find(
                  (d) =>
                    category(d.id) === group &&
                    !attributes.includes(d.id) &&
                    d.status === "implemented" &&
                    !proposal.draft.purchases?.some(
                      (p) => p.definition_id === d.id,
                    ),
                );
                if (definition)
                  change({
                    ...proposal.draft,
                    purchases: [
                      ...(proposal.draft.purchases ?? []),
                      {
                        definition_id: definition.id,
                        amount: definition.kind === "attribute" ? 10 : 1,
                      },
                    ],
                  });
              }}
            >
              Add {entryNoun[group]}
            </Button>
            {group === "Equipment" && (
              <p>
                Starting equipment is also checked against the adventure’s
                inventory when the party is activated.
              </p>
            )}
          </details>
        ))}
      </fieldset>
      <section aria-label="Derived statistics" className="derived-preview">
        <h3>Derived statistics</h3>
        {current && current.derived.length > 0 ? (
          <dl>
            {orderStats(current.derived, ([target]) => target).map(
              ([target, value]) => (
                <div key={target}>
                  <dt>{statLabel({ id: target }).full}</dt>
                  <dd>{value}</dd>
                </div>
              ),
            )}
          </dl>
        ) : (
          <p>
            {current
              ? "Resolve the build diagnostics to see derived statistics."
              : "Waiting for the current edit to be validated."}
          </p>
        )}
      </section>
    </div>
  );
}
