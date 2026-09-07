import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type SetStateAction,
} from "react";
import { Button } from "../components/ui/button";
import { definitionLabel, orderStats, statLabel } from "../presentation/labels";
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
export type ProposalChange = SetStateAction<Proposal>;
const groups = [
  "Attributes",
  "Advantages",
  "Disadvantages",
  "Skills",
  "Equipment",
] as const;

const repeatDelayMs = 400;
const repeatIntervalMs = 100;

function StepButton({
  label,
  disabled,
  onStep,
  children,
}: {
  label: string;
  disabled: boolean;
  onStep: () => void;
  children: string;
}) {
  const delay = useRef<ReturnType<typeof setTimeout> | null>(null);
  const interval = useRef<ReturnType<typeof setInterval> | null>(null);
  const suppressClick = useRef(false);
  const resetSuppression = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onStepRef = useRef(onStep);
  useEffect(() => {
    onStepRef.current = onStep;
  }, [onStep]);

  const stopRepeat = useCallback(() => {
    if (delay.current !== null) clearTimeout(delay.current);
    if (interval.current !== null) clearInterval(interval.current);
    delay.current = null;
    interval.current = null;
  }, []);
  const finishPointerStep = useCallback(() => {
    stopRepeat();
    if (resetSuppression.current !== null)
      clearTimeout(resetSuppression.current);
    resetSuppression.current = setTimeout(() => {
      suppressClick.current = false;
      resetSuppression.current = null;
    }, 0);
  }, [stopRepeat]);

  useEffect(
    () => () => {
      stopRepeat();
      if (resetSuppression.current !== null)
        clearTimeout(resetSuppression.current);
    },
    [stopRepeat],
  );
  useEffect(() => {
    if (disabled) {
      suppressClick.current = false;
      stopRepeat();
    }
  }, [disabled, stopRepeat]);

  return (
    <Button
      type="button"
      className="step-button"
      aria-label={label}
      disabled={disabled}
      onPointerDown={(event) => {
        if (event.button !== 0 || delay.current !== null) return;
        suppressClick.current = true;
        onStepRef.current();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        delay.current = setTimeout(() => {
          onStepRef.current();
          interval.current = setInterval(
            () => onStepRef.current(),
            repeatIntervalMs,
          );
        }, repeatDelayMs);
      }}
      onPointerUp={finishPointerStep}
      onPointerCancel={() => {
        suppressClick.current = false;
        stopRepeat();
      }}
      onLostPointerCapture={stopRepeat}
      onClick={() => {
        if (suppressClick.current) {
          suppressClick.current = false;
          if (resetSuppression.current !== null)
            clearTimeout(resetSuppression.current);
          resetSuppression.current = null;
          return;
        }
        onStepRef.current();
      }}
    >
      {children}
    </Button>
  );
}

export function CharacterDraftEditor({
  proposal,
  onChange,
  preview,
  disabled = false,
  templates = [],
}: {
  proposal: Proposal;
  onChange: (change: ProposalChange) => void;
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
  const change = (draft: Proposal["draft"]) => onChange({ ...proposal, draft });
  const stepPurchase = (index: number, delta: number) =>
    onChange((current) => ({
      ...current,
      draft: {
        ...current.draft,
        purchases: current.draft.purchases.map((purchase, purchaseIndex) =>
          purchaseIndex === index
            ? {
                ...purchase,
                amount: Math.min(
                  10000,
                  Math.max(1, (purchase.amount ?? 1) + delta),
                ),
              }
            : purchase,
        ),
      },
    }));
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
            {proposal.draft.purchases.map((p, i) =>
              category(p.definition_id) !== group ? null : (
                <div key={i} className="context-actions purchase-row">
                  <label htmlFor={`${id}-purchase-${i}`}>Ability {i + 1}</label>
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
                    {catalog.map((d) => (
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
                    <StepButton
                      label={`Decrease ${definitionLabel(p.definition_id)}`}
                      disabled={p.amount <= 1}
                      onStep={() => stepPurchase(i, -1)}
                    >
                      −
                    </StepButton>
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
                    <StepButton
                      label={`Increase ${definitionLabel(p.definition_id)}`}
                      disabled={p.amount >= 10000}
                      onStep={() => stepPurchase(i, 1)}
                    >
                      +
                    </StepButton>
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
                    Remove ability {i + 1}
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
              ),
            )}

            <Button
              type="button"
              disabled={
                !catalog.some(
                  (d) => category(d.id) === group && d.status === "implemented",
                )
              }
              onClick={() => {
                const definition = catalog.find(
                  (d) =>
                    category(d.id) === group &&
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
              Add{" "}
              {group === "Attributes"
                ? "attribute"
                : group === "Skills"
                  ? "skill"
                  : group === "Equipment"
                    ? "equipment"
                    : group === "Advantages"
                      ? "advantage"
                      : "disadvantage"}
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
