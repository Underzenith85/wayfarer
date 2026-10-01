# Armoury restoration source review

Issue #818 uses Characters, Fourth Edition third printing (February 2008),
SHA-256 `872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`,
and Campaigns, Fourth Edition fourth printing,
SHA-256 `79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
The supplied PDF bytes were reopened and their hashes checked on 2026-10-01.

Characters B178 makes Armoury IQ/A, TL-indexed, with separate Melee Weapons
and Body Armor specialties. Characters B168 distinguishes IQ-based technological
skills from other technological skills: working one, two or three TLs above
training gives -5, -10 or -15; four or more is impossible. Working one TL below
training gives -1, then another -2 for each further lower TL. The approved
purchase supplies the training TL; the actual equipment supplies its TL.
Unknown or nonnumeric TL cannot authorize these restoration procedures.

Campaigns B484–485 confirms the existing equipment-repair transaction: each
attempt takes half an hour; success restores margin HP, minimum one, capped at
missing HP. Its price modifiers are +1 through $1,000, zero through $10,000,
then -1/-2/-3 at the printed higher bands. Zero/negative HP requires spare parts
worth 1d times 10% of original price and an additional -2. Destroyed equipment
cannot be repaired. Existing transactions preserve owned tools/materials,
shared-clock deadlines, authority, stale-command rejection and exact retries.

`tests/test_issue_818_armoury_repairs.py` checks both actual equipment kinds,
printed numerical TL boundaries, HP results, rejection and retry. Existing
`tests/test_object_combat.py` retains the price/major-repair/material coverage.
The source-derived fixtures use IQ-based TL purchases rather than the former
DX-based non-TL test skill.

This review does not certify the general technology skill family, other Armoury
specialties, familiarity variants, API/UI journeys or the whole Basic Set.
