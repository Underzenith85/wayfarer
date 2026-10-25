# Unusual Background admission (issue #906)

## Source and scope

The source is *GURPS Basic Set: Characters*, Fourth Edition, third printing,
B96. The canonical identity remains `trait:advantage:unusual-background` and
its source reference remains the Characters catalog reference. B96 assigns the
GM the decision about whether an exceptional background has a tangible benefit,
what that benefit admits, and its cost in that campaign. The surcharge is in
addition to the admitted ability's own cost. Background narrative alone does
not create a purchased advantage.

This implementation supports one explicit, fixed-price campaign decision over
one or more existing, implemented trait, skill or technique definitions. The trusted host
uses `bind_unusual_background` with `UnusualBackgroundDecision` to create a new
rules-package identity/version. The record carries the authoring GM, narrative,
exact admitted definition IDs, fixed nonnegative price, allowed/forbidden
choice, and B96 reference. The selected package digest includes the entire
record. It is not a player draft option or an inferred physiological effect.

- A permitted, positive-cost decision adds the canonical background as a real
  prerequisite of each selected benefit. Its fixed price is charged once in the
  compiled build, in addition to the ordinary cost of each purchased benefit.
- A zero-cost decision leaves the ordinary benefits available without buying a
  background. A forbidden decision makes the selected benefits unavailable.
- Positive-cost backgrounds require recorded GM approval. A background without
  any configured tangible benefit in the build is blocked and cannot be
  overridden. Client-selected prices, levels, modifiers, unknown benefits, and
  unbound generic backgrounds fail closed.
- Skills and techniques retain their normal point cost and all existing acquisition
  prerequisites. A paid decision adds the background to both purchase and skill
  acquisition prerequisites, so attribute defaults and skill-to-skill defaults
  require the same purchased background. A forbidden skill is unavailable to
  both investment and defaults. A legal default unlocked by the surcharge is a
  tangible benefit and still requires recorded GM approval; a skill with no
  default needs an actual purchase. Unrelated skills keep their normal defaults.
- The background prerequisite applies to exactly the GM-selected benefits.
  Package validation rejects added admission links to other abilities, including
  zero-cost and forbidden decisions. Binding a new decision also rejects an
  existing unbound background prerequisite rather than inheriting permission
  that the decision's author never selected.
- The decision's author must be one of the trusted campaign GMs. The normal
  approval pipeline records approver, reason, build/rules/policy digests, and
  revision. Acting approvers must also currently hold the campaign GM seat,
  including transaction and retry boundaries. Historical source authorship is
  retained when that author leaves the seat; another trusted, seated GM can
  approve the unchanged decision. Decision or cost changes require an explicit package migration and
  invalidate the previous approval.

The host must persist the authored package artifact and retain its exact pin;
`canonical_json` includes the complete decision. The durable play checkpoint
stores the rules pin, purchases and recorded approval. Reconstructing the host
from the saved decision restores the same package digest; it cannot silently
substitute a new price under an existing checkpoint. This is an engine/host
construction, not a new player-facing authoring API.

## Acceptance evidence

`tests/test_unusual_background_admission.py` exercises the same ability under
common, charged and forbidden campaign decisions, fixed costs including values
outside the old client selector, actual totals/prerequisites, GM identity and
approval, pin/approval serialization, tamper rejection, and durable play.
The physiology reconciliation test preserves every one of the 267 mundane
identities exactly once. The frozen #822 denominator stays at 39: the historical
row remains there, with an explicit current family and #906 admission owner.
The admission tests also reject unselected prerequisite links and verify an
actual two-benefit build: Trained by a Master (30) and Combat Reflexes (15) with
one GM-selected 50-point background cost 95 points, with recorded GM approval.

The unconfigured global catalog now reports this row unsupported. A bound
campaign package provides the supported construction; a generic hook or family
label is not evidence that arbitrary variable constructions work. Conditional
costs, stacked independent backgrounds, cross-package benefits, new abilities,
and unimplemented benefit mechanics are not
inferred or promoted. `tests/test_background_skill_admission.py` exercises paid,
common and forbidden skill/technique decisions, attribute and skill defaults,
trained-parent technique defaults, AND/OR acquisition requirements, GM approval,
approval serialization, price migrations, and rejection of detached or weakened
acquisition prerequisites. Durable host tests cover approval retry, restart,
event replay and actual activation of an admitted skill default. The private
[Karate Kicking consumer](gurps-karate-kicking.md) also enforces a named technique
decision in actual kick declarations and scoring, while retaining the separate
ordinary DX kick. No universal
Basic Set certification claim or public schema/version change is made.
