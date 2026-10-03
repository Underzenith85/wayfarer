# Armoury restoration source review

Issue #818 uses Characters, Fourth Edition third printing (February 2008),
SHA-256 `872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`,
and Campaigns, Fourth Edition fourth printing,
SHA-256 `79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
The supplied bytes and printed B168/B178/B345–346/B483–485 were reopened on
2026-10-01. No licensed source text is bundled.

## Verified consumer behavior

Characters B178 makes Armoury IQ/A, TL-indexed, with separate Melee Weapons
and Body Armor specialties. Melee Weapons includes shields and weapons used
with Thrown Weapon skills. The repair binding accepts those classes and
rejects a body-armor specialty on an actual melee weapon.

B168's IQ technology table gives -5/-10/-15 for one/two/three higher TLs;
four higher TLs is impossible. One lower TL gives -1, with another -2 for
each additional lower TL. The approved purchase supplies training TL, and the
actual item supplies equipment TL. Nonnumeric TL rejects. B345 also prohibits
an effective nondefense skill below three; this is checked before spending
parts and again before the completion roll.

B484–485 gives a baseline half-hour attempt. A successful roll restores margin
HP, minimum one, capped at missing HP. Its price modifiers are +1 through
$1,000, zero through $10,000, then -1/-2/-3 at the printed higher bands.
Zero/negative HP requires parts worth a d6 times ten percent of the original
price and an additional -2. Destroyed items reject. Whole inventory units
round up using exact rational prices, including fractional-price boundaries.

The pinned owned toolkit's existing `GeneralEquipmentFeature(kind="tool")`
modifier for the exact repair skill now affects the actual repair check.
Unrelated skill features do not contribute. Duplicate matching features and
features requiring unimplemented consumption or operating-time handling reject
before randomness. This uses existing authored tool facts, not request-authored
skill bonuses. The ordinary unannotated pinned toolkit retains the basic +0.

## #818 acceptance checklist

- Actual weapon and body-armor HP, success margins, zero-margin minimum, cap at
  missing HP, ordinary/critical failure, and asymmetric trained-TL modifiers:
  `tests/test_issue_818_armoury_repairs.py`.
- Both consumers' major-repair price, materials, ownership, deadline and exact
  start/finish retry after process restart: `test_major_repairs_conserve_supplies_through_restart`
  in `tests/test_issue_818_armoury_acceptance.py`. Supplies cannot be consumed a
  second time by replay, and equipment/tool custody stays locked during work.
- Every printed price-band boundary changes actual HP through both consumers:
  `test_price_boundaries_change_actual_restoration`.
- Missing tools, insufficient parts, destroyed/undamaged items, wrong ownership,
  impossible or unknown TL, and below-three effective skill reject without
  invented repairs, inventory changes or dice: the acceptance file's negative tests.
- Fractional-price whole-unit conservation, shield/thrown-weapon coverage and
  source-pinned toolkit modifiers: the acceptance file's B178/B345 and
  fractional-price tests. Authored unsupported tool variants reject explicitly.
- Existing tests preserve wrong-principal rejection, stale revisions, exact
  receipts and item quantity/ownership. Every major completion is also checked
  against event replay, including failure.

## Verified scope and unverified variants

The checklist above covers #818's bounded connection of the two named effects
to actual repair transactions. Unsupported source-valid variants remain
visible and unverified; they do not receive source or certification promotion.

- [#976](https://github.com/Underzenith85/wayfarer/issues/976): the private
  [selected-time path](gurps-armoury-selected-time.md) now binds B346 extra-time
  and haste choices to actual deadlines and checks. Unselected tasks retain
  1,800 seconds; a longer wait alone does not earn a bonus.
- [#977](https://github.com/Underzenith85/wayfarer/issues/977): B345 tooling
  modifiers can be pinned through existing skill-specific tool
  features, but choosing among alternative or absent toolkits, deriving a new
  modifier from runtime damage/missing components, and consuming powered or
  expendable tooling are not implemented. A toolkit with explicit unsupported
  operating requirements is rejected rather than treated as free supplies.
- [#978](https://github.com/Underzenith85/wayfarer/issues/978): the private
  [verified-default path](gurps-armoury-defaults.md) binds current approved
  IQ/available matching-Engineer/cross-Armoury sources to a verified training TL
  and actual Body Armor/firearm repair outcomes. Wrong or unavailable specialties
  refuse; no Engineer (Body Armor) or substitute family is invented.

- [#975](https://github.com/Underzenith85/wayfarer/issues/975): the new private
  [recorded assessment](gurps-armoury-parts.md) admits stock sufficient for the
  actual immutable rolled requirement. Unassessed historical starts retain
  their maximum-cost preflight and original random stream.

Trusted-GM performer/model/specialty observations now apply the B178
unfamiliarity modifier to actual accepted repair checks, with resulting HP
oracles in `tests/test_armoury_familiarity.py`. Missing observations preserve
historical zero adjustment; they do not establish familiarity. Initial
allocation, eight-hour practice acquisition, and six-familiarity GM rolls
remain separate unimplemented acquisition work.

These variants require a reviewed authoritative input/state design. No frozen request
fields, API/UI flow, source-evidence promotion or completion flag is introduced.
Other Armoury specialties and general technology/familiarity certification also
remain outside this implementation.
