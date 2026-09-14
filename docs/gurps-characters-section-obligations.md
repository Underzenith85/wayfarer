# Characters section obligation review (#678)

This review covers the 161 section-ledger rows that blocked certification at
`main@766a19d880083f1bffe8f89cf0a84d684e91beab`. The checked source is
*Basic Set: Characters*, Fourth Edition, third printing (February 2008), SHA-256
`872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`.
The review changes no section identity, printed page, parent, profile membership,
or source denominator.

## Disposition policy

Each reviewed row carries `obligation_review_issue: 678` and exactly one explicit
obligation:

- `executable-mechanic` is a rule that changes construction or play. It is either
  joined to an existing executable test or remains a blocker assigned to a
  bounded open issue.
- `construction-catalog` indexes choices, lists, tables, or authoring guidance.
  The section heading has no independent runtime transition; item-level
  inventories continue to own the listed mechanics.
- `reference-only` is explanatory, introductory, conversion, or play guidance
  that defines no engine state transition.
- `structural-non-runtime` is a chapter, appendix, or supplied form heading. Its
  children retain their own independent obligations.
- `profile-excluded` is reserved for a reviewed profile boundary. No newly
  reviewed row required that disposition; previously disabled optional-rule rows
  retain their existing profile decisions outside this 161-row batch.

The result is 67 construction/catalog rows, 68 executable rows, 15 reference
rows, and 11 structural rows. Of the executable rows, 43 are joined to existing
independent behavior tests and 25 remain visible certification blockers.

## Existing executable evidence

The following implemented families are joined without treating this review as
runtime evidence:

- Skill construction, defaults, specialties, familiarity, TL rules, and
  techniques: `tests/test_skills.py` and `tests/test_contextual_metadata.py`.
- Spell acquisition, casting, timing, energy, interruption, maintenance,
  cancellation, rituals, and area selection: `tests/test_spells.py`,
  `tests/test_spell_bindings.py`, and `tests/test_magic_protocols.py`.
- Psionic ability execution and advancement inside an acquired power:
  `tests/test_psi_powers.py`.
- Money, purchasing, cost of living, and legality: `tests/test_economics.py` and
  `tests/test_campaign_administration.py`.

Catalog section rows do not certify the listed traits, modifiers, skills,
spells, or equipment. Those items remain independently accountable through the
runtime inventory and exact trait/modifier source ledgers.

## Remaining executable owners

| Owner | Exact section obligations |
| --- | --- |
| #700 | Advantage origins; Potential Advantages; turning advantages on/off; Frequency of Appearance; Limited Defenses; Alternative Attacks; Secret Disadvantages; self-control; self-imposed disadvantages; buying off disadvantages |
| #682 | Attack enhancement/limitation composition; turning enhancements on/off |
| #683 | Gadget limitations |
| #684 | Silver weapons; shields |
| #685 | Bodkin points; hand grenades/incendiaries; smartgun electronics |
| #691 | Limits on effect; spell classes; magic staffs; dissipating held spells; long-distance modifiers; psi/magic interactions |
| #693 | Cross-species physiology modifiers and their specialty bypasses |

Issues #691, #693, and #700 were opened by this review because the selected
printing exposes residual magic/psi interactions, cross-species skill
procedures, and section-level trait procedures that were neither fully evidenced
nor owned by another bounded completion issue. No unresolved Characters section
row falls back to roadmap #94.
